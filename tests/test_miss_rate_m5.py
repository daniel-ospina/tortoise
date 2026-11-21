"""Pins M5's arithmetic, its invariants, and the artifact its record cites.

The evidence record (`docs/evidence/14015-m5-miss-rate/README.md`) publishes
numbers `#13989`'s allowance sizing will quote, and they sit on an instrument
whose failure mode is SILENT — the curve is arithmetic on a mass distribution, so
an inverted ordering, a sampler seeded from the same stream as the eviction order,
or a curve read off the wrong population still produces a table of plausible
rates. Three things are pinned here, each closing a different way the record rots:

1. **The arithmetic.** ``mass_order`` / ``resident_ids`` / ``weighted_sample`` /
   ``covered_mass`` / ``coverage_for`` / ``marginal_pp_per_mb`` /
   ``flattening_point`` / ``mb_for_miss`` are pure, so their contract is asserted
   directly — including the seed independence whose absence made M2's first
   control arm report a DISJOINT subset instead of an independent one, the defect
   this instrument inherits the offset for.
2. **The invariants the instrument refuses on.** ``miss`` must equal its two
   halves; a demoted node must never be returned; every query's canonical and every
   id the leg returns must be a node of the population the instrument BUILT; and a
   COMPLETE arm cannot miss every query. The checks live in ``run()``, which needs a
   live FalkorDB, so the guards themselves are module-level PURE functions
   (``check_query_ids`` / ``check_leg_ids`` / ``check_arm``) driven directly in
   section 1b below — a guard nested in a closure could only be exercised against a
   graph, so nothing could prove it FIRES. Where the bundle is committed, section 2
   re-checks the same properties on the ARTIFACT a reader will quote.

   ⛔ **One thing here is NOT an invariant and must not be re-added as one:**
   ``fault_in_rate == 1 − resident_query_share``. Both sides are counts over
   complementary slices of the SAME ``rows`` list, so ANY change to the residency
   predicate moves both together and no mutation can make the check fire
   (``#14162`` F3). It was asserted in ``run()`` and has been deleted there; what
   it claimed to detect is now a cross-check against the population, which the arm
   cannot move.
3. **The record against the artifact.** The README's tables are recomputed from
   ``measurement.json`` CELL BY CELL, at the row and column each figure is printed
   under — membership is not enough, because the ``:Point`` peak is a real artifact
   value that was nonetheless printed under the wrong doubling interval. Two figures
   CANNOT be recomputed and are not claimed: §10's reproducibility band (it needs
   two re-runs) and the ``:Point`` floor's M2 column (M2's measurement, cited).

⛔ **The evidence bundle IS committed, and the declared-absence guard is gone.**
``docs/evidence/14015-m5-miss-rate/measurement.json`` landed in a later commit on
this branch; ``_artifact()`` simply reads it, and
``test_the_committed_bundle_makes_the_artifact_backed_pins_run`` holds the removal
so a withdrawn bundle reds here instead of quietly putting the pins back to sleep.

⛔ None of this runs the measurement — it needs a live FalkorDB and a real capture
corpus. A test that needed a graph would be skipped on CI and would guard nothing.
What is pinned is the reasoning surface and the committed artifact.

⛔ And one boundary this file exists to keep: **M5 must never restate M2's
numbers.** M2 measured retrieval QUALITY at a window (recall@3, found@deep); M5
measures the MISS RATE that window produces. ``test_the_bundle_does_not_restate_m2s_numbers``
holds that line, because "M2 says recall halves, so M5's miss rate is half" is the
exact conflation the two issues exist to separate.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "14015-m5-miss-rate"
ARTIFACT = EVIDENCE / "measurement.json"
README = EVIDENCE / "README.md"
TOOL = ROOT / "tools" / "miss_rate_m5.py"
M2_ARTIFACT = ROOT / "docs" / "evidence" / "12186-m2-windowed-quality" / "measurement.json"
ARCH = ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md"
ARCH_CHANGELOG = ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE-CHANGELOG.md"
PRICING = ROOT / "product" / "pricing.json"

sys.path.insert(0, str(ROOT))

from tools.miss_rate_m5 import (  # noqa: E402
    CURVE_COVERAGES,
    DEEP_LIMIT,
    EVICT_SEED_OFFSET,
    FLAT_PP_PER_MB,
    GRAPH_COVERAGES,
    MISS_BARS,
    SENSITIVITY_EXPONENTS,
    SIZES_MB,
    TERM_MIN_LEN,
    TERM_STRIP,
    TIERS,
    _span_mass,
    check_arm,
    check_leg_ids,
    check_query_ids,
    coverage_for,
    coverage_for_miss,
    covered_mass,
    doubling_gain,
    fit_exponent,
    flattening_coverage,
    flattening_point,
    largest_tenant_mb,
    lorenz,
    marginal_pp_per_mb,
    mass_order,
    mb_for_miss,
    resident_ids,
    terms_of,
    weighted_sample,
    windows_table,
    zipf_masses,
)


def _artifact() -> dict:
    """The committed evidence bundle.

    ⛔ It was DECLARED ABSENT here while `ce0bf1bf`'s test file landed one commit
    ahead of the artifact (`#14162` F2's class: a control that cannot run). The
    bundle is committed now, so the guard is GONE and every artifact-backed pin
    below runs — `test_the_committed_bundle_makes_the_artifact_backed_pins_run`
    holds that, and it reds if the bundle is ever withdrawn without this docstring
    following it.
    """
    return json.loads(ARTIFACT.read_text())


# ── 1. the arithmetic ────────────────────────────────────────────────────────

def test_resident_ids_keeps_the_hottest_suffix_and_complete_keeps_all():
    order = [f"n{i}" for i in range(10)]
    assert resident_ids(order, 1.0) == order
    kept = resident_ids(order, 0.5)
    assert kept == order[5:], "the retained suffix is the hottest half"
    # A coverage that would round to zero keeps one node, not none: "the graph is
    # empty" is a different experiment from "a very small window".
    assert len(resident_ids(order, 0.001)) == 1
    with pytest.raises(ValueError):
        resident_ids(order, 1.5)


def test_mass_order_is_coldest_first_and_total_under_ties():
    masses = {"a": 5, "b": 5, "c": 1, "d": 9}
    order = mass_order(masses, "mass", seed=0)
    assert order[0] == "c", "the coldest node goes first"
    assert order[-1] == "d", "the hottest node is kept last"
    # Ties break by id, so the order is TOTAL — a Counter's insertion order is
    # stable within one run but is not a property to depend on across runs.
    assert order.index("a") < order.index("b")
    # coverage 0.5 of four nodes keeps TWO: the two hottest, i.e. b and d.
    assert set(resident_ids(order, 0.5)) == {"b", "d"}


def test_uniform_order_is_a_permutation_and_never_a_subset():
    masses = {f"n{i}": i + 1 for i in range(50)}
    order = mass_order(masses, "uniform", seed=7)
    assert sorted(order) == sorted(masses), "an eviction order is a permutation"


def test_the_sampler_and_the_eviction_stream_are_independent():
    """⛔ M2's measured defect, inherited as a guard.

    The query sample and the ``uniform`` eviction order are both derived from the
    population list. Seeded from the SAME value they would be the same permutation,
    so the retained suffix and the sampled prefix would be DISJOINT BY
    CONSTRUCTION — M2's first run reported ``resident_share`` 0.667 / 0.167 /
    0.000 this way. The offset separates the streams; this test asserts the
    separation is real rather than documented.
    """
    rows = [{"id": f"n{i}", "mass": 1} for i in range(200)]
    sample = weighted_sample(rows, 40, None, seed=11)
    order = mass_order({r["id"]: 1 for r in rows}, "uniform", seed=11)
    kept = set(resident_ids(order, 0.20))
    overlap = len({r["id"] for r in sample} & kept)
    # Independent subsets of 40 from 200 overlap ~8 on average. A shared seed
    # gives 0.
    assert overlap > 0, (
        "the query sample and the eviction order are disjoint — the sampler and "
        "the eviction stream share a seed; EVICT_SEED_OFFSET is not being applied")
    assert EVICT_SEED_OFFSET == 1_000_003


def test_weighted_sample_draws_without_replacement_and_favours_mass():
    rows = [{"id": f"n{i}", "mass": (1000 if i < 5 else 1)} for i in range(500)]
    drawn = weighted_sample(rows, 20, "mass", seed=3)
    ids = [r["id"] for r in drawn]
    assert len(set(ids)) == len(ids), "sampling is WITHOUT replacement"
    assert len(ids) == 20
    # The heavy head must dominate the draw — that is the whole point of the
    # weighted mode. There are only FIVE heavy nodes, so "dominates" means all of
    # them are in a draw of 20 while a weight-blind sampler would take ~0.02.
    heavy = sum(1 for i in ids if int(i[1:]) < 5)
    assert heavy == 5, f"only {heavy} of the 5 heavy nodes were drawn"
    blind_expectation = 20 * 5 / 500
    assert heavy > blind_expectation * 10, (
        "the sampler is behaving like a uniform draw")


def test_weighted_sample_clamps_zero_mass_so_no_node_is_impossible():
    """A zero-recurrence node must still be DRAWNABLE.

    If it were impossible to query, ``fault_in_rate`` would be a rate over a
    population narrowed by the INSTRUMENT rather than by the corpus.
    """
    rows = [{"id": "zero", "mass": 0}] + [{"id": f"n{i}", "mass": 1} for i in range(30)]
    # Force the sampler to consider everyone, then check 'zero' can appear.
    seen = set()
    for seed in range(200):
        seen |= {r["id"] for r in weighted_sample(rows, 25, "mass", seed=seed)}
    assert "zero" in seen


def test_covered_mass_is_monotone_and_bounded_by_one():
    ordered = sorted(zipf_masses(1000, 1.0), reverse=True)
    vals = [covered_mass(ordered, f) for f in CURVE_COVERAGES]
    assert vals == sorted(vals), "a hotter window cannot cover LESS mass"
    assert vals[0] > 0.0 and vals[-1] == 1.0
    with pytest.raises(ValueError):
        covered_mass(ordered, -0.1)


def test_the_lorenz_curve_lies_above_the_identity():
    """INVARIANT 5, asserted in pure arithmetic.

    ⛔ The identity is ``covered_mass`` over an EQUAL-mass vector of the same
    length, NOT ``f`` itself. ``covered_mass`` rounds the retained node COUNT, so
    an equal-mass vector can legitimately sit a little below ``f`` (5 nodes at
    coverage 0.25 keeps 1 and covers 0.20). Comparing against ``f`` would be a
    slightly WRONG baseline, and a pin with a wrong baseline either weakens or
    misfires — which is exactly the shape ``#14162`` F2 item 2 recorded.
    """
    for exponent in SENSITIVITY_EXPONENTS:
        ordered = sorted(zipf_masses(500, exponent), reverse=True)
        identity = [covered_mass([1] * len(ordered), f) for f in CURVE_COVERAGES]
        curve = lorenz(ordered)
        assert len(curve) == len(CURVE_COVERAGES)
        for point, base in zip(curve, identity, strict=True):
            assert point["covered_mass"] >= base - 1e-12, (
                f"Lorenz below the identity at exponent={exponent} "
                f"coverage={point['coverage']}")
            assert point["fault_in_rate"] == pytest.approx(
                1.0 - point["covered_mass"], abs=1e-6)


def test_invariant_5_refuses_a_curve_built_from_an_inverted_ordering():
    """⛔ NON-VACUITY: the exact bug INVARIANT 5 names, driven as a REFUSAL.

    Without the assertion inside ``lorenz()`` the reversed vector returns a
    perfectly plausible — and meaningless — curve. `#14162` F2 recorded that the
    invariant was described under a header claiming it was asserted in ``run()``
    while no code compared anything against anything; a test that only re-derives
    the property does not prove the instrument CHECKS it.
    """
    ordered = sorted(zipf_masses(200, 1.2), reverse=True)
    with pytest.raises(RuntimeError, match="INVARIANT 5") as exc:
        lorenz(sorted(ordered))  # hottest FIRST -> hottest LAST
    assert "coverage" in str(exc.value), "a refusal that cannot say WHERE is inert"


def test_coverage_for_is_the_window_over_the_tenant_size_and_never_exceeds_one():
    assert coverage_for(77, 150) == pytest.approx(0.5133, abs=1e-4)
    assert coverage_for(615, 150) == 1.0, "a team window over a 150 MB graph is complete"
    assert coverage_for(0, 150) == 0.0
    with pytest.raises(ValueError):
        coverage_for(77, 0)


def test_the_coverage_map_needs_no_bytes_per_node():
    """The window is expressed in MB and the tenant size in MB, so the coverage is
    dimensionless — the §17.4.2 byte dispute (1,024 declared vs 1,672 measured)
    cannot move the curve, only node counts. Pinned because that is a claim the
    record makes and a reader may doubt."""
    # ⛔ REACHABLE (#14162 F5): this used to read
    #     `coverage_for(77, 150) == coverage_for(77, 150)`
    # — the same deterministic call on both sides, which stayed green while the
    # function's semantics were mutated (`2 * window_mb / size_mb`). Two tenants
    # with the same MB ratio but different node counts (1,024 vs 1,672 B/node, the
    # §17.4.2 disagreement) must get the same coverage, and that same number must
    # be the plain ratio: no bytes-per-node term can enter it.
    assert coverage_for(77, 150) == coverage_for(154, 300)
    assert coverage_for(77, 150) == pytest.approx(77 / 150, abs=1e-12)
    # No bytes-per-node parameter exists to disagree about.
    import inspect
    params = inspect.signature(coverage_for).parameters
    assert set(params) == {"window_mb", "size_mb"}


def test_marginal_is_positive_and_falls_as_the_window_grows():
    ordered = sorted(zipf_masses(2000, 1.0), reverse=True)
    marg = marginal_pp_per_mb(lorenz(ordered, CURVE_COVERAGES), 150)
    assert marg, "a curve with >= 2 points has marginals"
    assert all(m["pp_per_mb"] > 0 for m in marg), (
        "every extra MB removes some miss rate; a non-positive marginal means the "
        "curve is inverted")
    first, last = marg[0]["pp_per_mb"], marg[-1]["pp_per_mb"]
    assert first > last, "the first MB buys more than the last (diminishing returns)"


def test_flattening_point_needs_a_criterion_and_reports_none_honestly():
    ordered = sorted(zipf_masses(1000, 1.0), reverse=True)
    flat = flattening_point(lorenz(ordered), 150)
    assert flat is not None
    assert flat["threshold_pp_per_mb"] == FLAT_PP_PER_MB
    assert 0 < flat["w_mb"] <= 150
    # ⛔ The uniform-query control NEVER flattens: its marginal is constant, so no
    # point has every later step below the threshold. `None` is the honest answer
    # and must never be coerced to 0.
    uniform = [{"coverage": f, "fault_in_rate": 1.0 - f} for f in CURVE_COVERAGES]
    assert flattening_point(uniform, 150) is None


def test_mb_for_miss_reports_none_when_the_bar_is_unreachable():
    ordered = sorted(zipf_masses(1000, 1.0), reverse=True)
    curve = lorenz(ordered)
    assert mb_for_miss(curve, 150, 0.50) is not None
    # Nothing can reach a negative rate, so `None` — never a fabricated 0 or the
    # largest window.
    assert mb_for_miss(curve, 150, -0.01) is None


def test_zipf_masses_are_descending_integers_at_least_one():
    for exponent in SENSITIVITY_EXPONENTS:
        m = zipf_masses(100, exponent)
        assert len(m) == 100
        assert m == sorted(m, reverse=True)
        assert min(m) >= 1, "a zero mass would be clamped by the sampler; the two must agree"


def test_fit_exponent_recovers_the_generator_it_was_given():
    """The sensitivity sweep is only meaningful if the fit can FIND the exponent.

    A fit that always returned the same value would make every sensitivity row
    identical while looking like a range.
    """
    for exponent in (0.8, 1.0, 1.2):
        assert fit_exponent(zipf_masses(1000, exponent)) == pytest.approx(exponent, abs=0.15)


def test_windows_table_reports_zero_for_a_fully_covered_tenant():
    ordered = sorted(zipf_masses(1000, 1.0), reverse=True)
    table = windows_table(lorenz(ordered), (75, 150), {"solo": 77, "team": 615})
    # A 615 MB window over a 75 MB tenant evicts NOTHING, so its miss rate is 0 by
    # construction — not a measurement of anything, which the record says.
    assert table["75"]["team"]["coverage"] == 1.0
    assert table["75"]["team"]["fault_in_rate"] == 0.0
    # ...and a 77 MB window over a 150 MB tenant is the interesting cell.
    assert 0.0 < table["150"]["solo"]["coverage"] < 1.0


# ── 1e. the shipped windows come from the price artifact of record ───────────
# ⛔ These two need NO bundle: they are what makes "the windows are read, never
# copied" true at EVERY head, including the ones where section 2 is declared
# absent. The artifact-gated sibling below (`..._in_the_artifact_...`) checks the
# same claim on the RECORD; this pair checks it on the TOOL, which is the thing
# that would drift first.

def test_the_windows_are_read_from_the_price_artifact_and_are_the_shipped_values():
    pricing = json.loads(PRICING.read_text())
    shipped = {t: int(pricing["tiers"][t]["max_graph_storage_mb"]) for t in TIERS}
    assert TIERS == ("solo", "pro", "team"), (
        "free/anon publish hot capacity 0 — there is no window to measure, so they "
        "are the declared exception rather than a swept zero row")
    assert shipped == {"solo": 77, "pro": 102, "team": 615}, shipped
    assert all(w > 0 for w in shipped.values())


def test_the_tool_holds_no_copy_of_a_window_value():
    """The registration above is worthless if a literal beside it can disagree.

    ⛔ The pre-fix shape of this test was `f"= {value}\n" not in source`, which
    caught ONE SPELLING. Measured: appending
    `WINDOW_DEFAULTS = {"solo": 77, "pro": 102, "team": 615}` to the tool — a
    second copy of all three windows, in a shape code would actually read — left
    the suite green. So the tool's AST is walked and ANY numeric literal equal to
    a shipped window is refused, wherever it sits: a dict, a tuple, a default
    argument, a comparison. The prose counts in the tool's docstrings are prose, so
    string constants are out of scope by construction — a literal that can DRIVE
    the instrument is what a second source of truth looks like.
    """
    pricing = json.loads(PRICING.read_text())
    shipped = {int(pricing["tiers"][t]["max_graph_storage_mb"]) for t in TIERS}
    source = TOOL.read_text()
    copied = sorted(
        (node.lineno, node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant)
        and not isinstance(node.value, bool)
        and isinstance(node.value, (int, float))
        and node.value in shipped
    )
    assert not copied, (
        f"the tool carries a shipped window as a literal at line(s) "
        f"{[(line, value) for line, value in copied]} — `PRICING` is the one "
        f"owner of the windows, and a copy beside it silently disagrees the "
        f"moment the price artifact moves")
    assert ('PRICING = Path(__file__).resolve().parent.parent / "product" / '
            '"pricing.json"') in source, (
        "the price artifact of record is no longer the one owner of the windows")


# ── 2. the committed artifact satisfies the invariants ───────────────────────

def test_the_committed_artifact_decomposes_its_own_miss_rate():
    """INVARIANTS 1 and 3, checked on the artifact a reader will quote.

    ``miss == fault_in_rate + miss_resident × resident_query_share`` and
    ``fault_in_rate == 1 - resident_query_share``. If either drifts, the two
    halves were counted over different denominators and the split the record
    publishes is not the split the measurement took.
    """
    art = _artifact()
    checked = 0
    for label, block in art["labels"].items():
        for arm, row in block["arms"].items():
            assert row["fault_in_rate"] == pytest.approx(
                1.0 - row["resident_query_share"], abs=1e-6), f"{label}/{arm}"
            assert row["miss"] == pytest.approx(
                row["fault_in_rate"] + row["miss_resident"] * row["resident_query_share"],
                abs=1e-6), f"{label}/{arm}: the decomposition does not add up"
            checked += 1
    assert checked >= 10, "the artifact carries the arms"


def test_the_committed_artifact_never_returns_a_demoted_node():
    """INVARIANT 2 — the strong form, per demoted query, not hidden under a ceiling."""
    art = _artifact()
    for label, block in art["labels"].items():
        for arm, row in block["arms"].items():
            assert row["found_deep_of_demoted"] == 0.0, (
                f"{label}/{arm} returned a demoted node at depth {DEEP_LIMIT}")
        complete = block["arms"]["complete|mass"]
        assert complete["resident_query_share"] == 1.0
        assert complete["fault_in_rate"] == 0.0, (
            "the complete arm demotes nothing, so its fault-in rate is 0 by "
            "definition — a non-zero value means the arm is mislabelled")


def test_the_committed_artifact_delivers_the_coverage_it_requests():
    """INVARIANT 4 — every windowed arm's graph held the set it names.

    The tolerance is one NODE, not a constant: the resident set is
    ``round(n × coverage)``, so on a population that is not divisible by the
    denominator the delivered share is off by up to ``1/n`` (measured: 533 of 711
    is 0.7496, not 0.75). A tighter tolerance would red on correct arithmetic.
    """
    art = _artifact()
    for label, block in art["labels"].items():
        tolerance = 1.0 / block["nodes"] + 1e-9
        for arm, row in block["arms"].items():
            if arm.startswith("complete"):
                continue
            assert row["coverage_delivered"] == pytest.approx(
                row["coverage_requested"], abs=tolerance), f"{label}/{arm}"


def test_the_committed_artifact_floor_is_the_complete_arm():
    """The floor the record quotes IS the complete arm's miss, resolved rather
    than asserted — so a re-run that changes the floor moves the claim with it."""
    art = _artifact()
    for block in art["labels"].values():
        assert block["floor"]["miss_resident"] == pytest.approx(
            block["arms"]["complete|mass"]["miss_resident"], abs=1e-9)


def test_the_windows_in_the_artifact_are_the_shipped_prices():
    """⛔ The window is not changed by this measurement. The artifact must carry
    the SHIPPED values, read from the price artifact of record."""
    art = _artifact()
    pricing = json.loads(PRICING.read_text())
    for tier in TIERS:
        assert art["windows"]["mb"][tier] == int(
            pricing["tiers"][tier]["max_graph_storage_mb"]), (
            f"{tier}: the artifact's window disagrees with product/pricing.json")
    assert art["windows"]["mb"]["solo"] == 77
    assert art["windows"]["mb"]["team"] == 615


# The artifact's TOP-LEVEL shape, closed on purpose. #13989's rule is "per label,
# never aggregate", and a top-level row is exactly where a pooled figure would be
# added — so adding one is a deliberate edit that has to come past this list.
_TOP_LEVEL_KEYS = {
    "cleanup", "consumed_by", "corpus", "coverage_map", "document",
    "full_corpus_curves", "generated_at", "git_head", "issue", "labels",
    "labels_measured", "measurement", "params", "provenance", "retrieval",
    "uniform_query_control", "windows", "⛔not_this",
}

# Keys that NAME a pooled figure. Exact matches only: `total` and `total_mass`
# are one population's sums, not a cross-label rate.
_POOLED_KEY_NAMES = {
    "aggregate", "aggregated", "all", "combined", "pooled", "both",
    "overall", "lumped", "labels_combined", "mean_across_labels",
    "across_labels",
}


def test_the_artifact_names_its_label_set_and_refuses_an_aggregate():
    art = _artifact()
    assert set(art["labels"]) == {"point", "object"}
    # #13989's rule: per label, never aggregate. ⛔ The pre-fix shape iterated
    # `art["labels"]` and asserted those KEYS aren't named "aggregate" — a loop
    # over the two label names that could never look at anything else. Measured:
    # adding a top-level `"aggregate": {"miss": 0.31, ...}` left it green, i.e.
    # the prohibition was invisible at the level it would be violated. The rule is
    # about the artifact's SHAPE, so the shape is what is pinned.
    assert set(art) == _TOP_LEVEL_KEYS, (
        "the artifact's top-level shape changed: "
        f"added={sorted(set(art) - _TOP_LEVEL_KEYS)} "
        f"removed={sorted(_TOP_LEVEL_KEYS - set(art))} — a new top-level row is "
        "where a cross-label figure would land, and #13989 forbids one")
    pooled: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if str(key).lower() in _POOLED_KEY_NAMES:
                    pooled.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")

    walk(art, "$")
    assert not pooled, (
        f"the artifact pools the labels at {pooled} — #13989 publishes per label, "
        f"never across them")
    assert set(art["labels_measured"]) <= {"point", "object", "⛔not_measured"}
    assert "No aggregate across labels is published" in (
        art["labels_measured"]["⛔not_measured"]), (
        "the artifact must SAY that no aggregate exists, not merely not carry one")


def test_the_artifact_says_which_m5_it_is():
    """The brief's own trap: `STORAGE-ARCHITECTURE.md` §15's M5 is a DIFFERENT
    question (where the vector index lives, answered 2026-11-07)."""
    art = _artifact()
    assert art["measurement"] == "M5"
    assert "§15" in art["⛔not_this"]


def test_the_sensitivity_sweep_actually_varies():
    """A sensitivity block whose rows are identical is decoration. The flattening
    point must MOVE with the concentration exponent, or the "range" the record
    publishes is not a range."""
    art = _artifact()
    for label, block in art["labels"].items():
        rows = block["sensitivity"]["rows"]
        assert set(rows) == {str(e) for e in SENSITIVITY_EXPONENTS}
        covered = [r["fitted_curve_covered_mass"]["0.1"] for r in rows.values()]
        assert len(set(covered)) > 1, f"{label}: the sensitivity rows do not move"


# ── 3. provenance, and the record against the artifact ───────────────────────

def test_the_artifacts_tool_sha_matches_the_committed_instrument():
    """A DRIFT CHECK, not production evidence.

    Whoever edits `tools/miss_rate_m5.py` re-cuts the artifact in the same commit,
    so this pin names the CURRENT bytes. It cannot attest that a run produced the
    numbers — only a re-run can — and the README says so in those words.
    """
    art = _artifact()
    assert art["provenance"]["tool_sha256"] == hashlib.sha256(TOOL.read_bytes()).hexdigest()


def test_the_artifact_binds_its_params_and_its_corpus():
    art = _artifact()
    for key in ("points", "objects", "sample", "deep_limit", "graph_coverages",
                "curve_coverages", "policies", "query_weights", "sizes_mb", "seed",
                "flat_pp_per_mb", "miss_bars"):
        assert key in art["params"], f"params.{key} is not recorded"
    assert art["corpus"]["corpus"]["files"] > 0, "the corpus's own accounting"
    assert art["params"]["graph_coverages"] == list(GRAPH_COVERAGES)
    assert art["params"]["sizes_mb"] == list(SIZES_MB)
    assert art["params"]["miss_bars"] == list(MISS_BARS)


def test_the_artifact_declares_which_retrieval_leg_it_measured():
    """A `keyword_only` run is a figure about the LEXICAL leg. The artifact must
    say so rather than let a reader assume the fused read was measured.

    ⛔ The pre-fix shape was `mode in ("keyword_only", "hybrid")` — true for both
    legal values, so no edit inside the artifact's own vocabulary could red it, and
    `legs_seen.vector` was unpinned. Measured: setting the artifact to `hybrid`
    with `vector: true` (and the record's mode line to match) left it green while
    the sentence around it still called the figure lexical. So the mode is pinned
    against the LEGS it claims, against the embedder a dense leg needs, and against
    the sentence the record prints — in BOTH directions.
    """
    art = _artifact()
    retrieval = art["retrieval"]
    mode = retrieval["mode"]
    assert mode in ("keyword_only", "hybrid"), f"unknown retrieval mode {mode!r}"
    assert set(retrieval["legs_seen"]) == {"fts", "structural", "vector", "fallback"}
    dense = mode == "hybrid"
    assert retrieval["legs_seen"]["vector"] is dense, (
        f"mode={mode!r} with legs_seen.vector={retrieval['legs_seen']['vector']!r} "
        f"is a record contradicting itself about which legs ran")
    assert retrieval["has_embedder"] is dense, (
        f"mode={mode!r} with has_embedder={retrieval['has_embedder']!r}: a dense "
        f"leg needs an embedder, and a keyword-only run cannot have had one")
    readme = README.read_text()
    assert f"**Retrieval mode:** `{mode}`" in readme, (
        f"the record does not state the mode the artifact measured ({mode!r})")
    if mode == "keyword_only":
        assert "a figure about the **lexical** leg" in readme, (
            "a keyword-only figure is a claim about the LEXICAL leg and the record "
            "must say so, not leave a reader to assume the fused read was measured")
    else:
        # ⛔ The other direction, which the first shape of this pin missed: the
        # measured mutant set the artifact to `hybrid` + `vector: true` +
        # `has_embedder: true` and the record's MODE line to match, and the lexical
        # sentence was left standing — green, while the record called a merged-leg
        # figure lexical. Self-consistent artifact, contradicting prose.
        assert "a figure about the **lexical** leg" not in readme, (
            "the artifact says the dense leg contributed, and the record still calls "
            "the figure lexical — one of the two is wrong about which legs ran")


def test_the_recorded_git_head_names_a_commit_where_the_instrument_bytes_exist():
    """`provenance.tool_sha256` had a pin and `git_head` had NONE — it was written
    by the run and read by no test, so the recorded commit could go stale while the
    digest stayed green.

    Two halves, and only one of them can run everywhere. The RECORD half is
    unconditional: the README prints the head it was written at, and those twelve
    characters must be the artifact's own `git_head`. The GIT half verifies the
    bytes: at the recorded commit, `tools/miss_rate_m5.py` must hash to
    `tool_sha256` — the one check that can tell a genuine anchor from a stale one.
    It needs the commit to be IN the clone, and a history rewrite (this branch was
    rebased) or a shallow checkout can drop it; in that case the anchor is
    unverifiable HERE and this test says so instead of passing quietly, because a
    skip is visible in the output and a false pass is not.
    """
    art = _artifact()
    head = art["git_head"]
    assert re.fullmatch(r"[0-9a-f]{40}", head), (
        f"git_head is not a commit sha: {head!r} — a truncated or symbolic value "
        f"cannot be resolved to the instrument's bytes")
    assert f"`{head[:12]}`" in README.read_text(), (
        f"the record must print the head it was written at (`{head[:12]}`); it is "
        f"the reader's only pointer to the run the figures came from")
    recorded = subprocess.run(
        ["git", "cat-file", "-e", f"{head}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True)
    if recorded.returncode != 0:
        pytest.skip(
            f"git_head {head[:12]} is not in this clone (a rebased history or a "
            f"shallow checkout), so the anchor cannot be verified here — the "
            f"re-cut commits the artifact and re-anchors it at a reachable head")
    blob = subprocess.run(
        ["git", "show", f"{head}:tools/miss_rate_m5.py"],
        cwd=ROOT, capture_output=True, check=True).stdout
    assert hashlib.sha256(blob).hexdigest() == art["provenance"]["tool_sha256"], (
        "the instrument's bytes at the recorded git_head are NOT the bytes "
        "provenance.tool_sha256 names — the anchor is stale or the digest was "
        "hand-edited away from the run that produced it")


def test_the_run_head_is_a_commit_that_could_have_produced_this_artifact():
    """The ANCHOR and the RUN are two different commits, and the pair is pinned.

    `git_head` is an anchor a re-cut may move; `provenance.run_git_head` is the
    commit whose bytes actually ran. Recording only the anchor let a re-cut claim a
    head that POSTDATES the artifact — measured on this branch before this pin:
    `git_head` was a commit dated 17:48:00Z while `generated_at` read 16:12:03Z, an
    artifact generated 1 h 36 m before its own instrument bytes existed, published
    in a record whose README said "the head the run was performed at". The digest
    pin above cannot see that, because it only ever compares (a) the sha printed in
    the README with (b) the blob at that sha — `git_head` WAS guarded against
    `tool_sha256` one commit earlier, but NOTHING compared the two members of the
    `generated_at`/`git_head` PAIR, and that pair is the one thing that proves a
    hand re-cut.

    Two halves, both from the run side, so an anchor can never satisfy them:
    the run commit's instrument bytes must hash to `run_tool_sha256`, and its commit
    time must not be AFTER `generated_at`.

    ⛔ **The never-later half is checked WITHOUT git, on purpose** (#2148 review):
    CI checks out at depth 1 (`python-ci.yml:968`), so no historical commit is in
    the clone, and a pin whose only home is a git probe SKIPS on the one surface
    that runs it — measured: with the record's run head replaced by `0`*40 the
    git-only version still reported `1 skipped`. So `provenance.run_committed_at`
    carries the run commit's own `%ct` and is compared to `generated_at`
    unconditionally, and the README half — which needs no history at all — runs
    BEFORE the skip, exactly as the sibling pin above does it. The git probes then
    CORROBORATE the recorded timestamp where history exists; they never gate it.
    """
    art = _artifact()
    prov = art["provenance"]
    run_head = prov["run_git_head"]
    assert re.fullmatch(r"[0-9a-f]{40}", run_head), (
        f"run_git_head is not a commit sha: {run_head!r}")

    generated = int(datetime.strptime(
        art["generated_at"], "%Y-%m-%dT%H:%M:%SZ")
        .replace(tzinfo=UTC).timestamp())

    # ---- UNCONDITIONAL HALF: no git needed, so CI cannot skip past it ----------
    committed = prov["run_committed_at"]
    assert isinstance(committed, int) and not isinstance(committed, bool), (
        f"provenance.run_committed_at must be an integer epoch, got {committed!r}")
    assert committed <= generated, (
        f"provenance.run_git_head {run_head[:12]} was committed "
        f"{committed - generated} s AFTER generated_at ({art['generated_at']}) — an "
        f"artifact cannot be produced by instrument bytes that did not exist yet, so "
        f"this field names an ANCHOR substituted for the run")

    # The record must SAY this, not only encode it: the README distinguishes the two
    # heads, so a reader quoting the figures is not told the anchor is the run.
    text = README.read_text()
    assert f"`{run_head[:12]}`" in text, (
        f"the record never prints the run head `{run_head[:12]}` — it prints only the "
        f"anchor, which is the substitution this pin exists to forbid")

    # ---- CORROBORATING HALF: only where the history is actually present --------
    exists = subprocess.run(
        ["git", "cat-file", "-e", f"{run_head}^{{commit}}"],
        cwd=ROOT, capture_output=True, text=True)
    if exists.returncode != 0:
        pytest.skip(
            f"the UNCONDITIONAL half ran and passed; only the git corroboration is "
            f"unavailable — run_git_head {run_head[:12]} is not in this clone (CI "
            f"checks out at depth 1, and this branch was rebased)")

    ran = subprocess.run(
        ["git", "show", f"{run_head}:tools/miss_rate_m5.py"],
        cwd=ROOT, capture_output=True, check=True).stdout
    assert hashlib.sha256(ran).hexdigest() == prov["run_tool_sha256"], (
        "the bytes at provenance.run_git_head do not hash to "
        "provenance.run_tool_sha256 — run_git_head does not name the instrument "
        "that produced these figures")

    actual = int(subprocess.run(
        ["git", "show", "-s", "--format=%ct", run_head],
        cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip())
    assert actual == committed, (
        f"provenance.run_committed_at says {committed} but {run_head[:12]} was "
        f"committed at {actual} — the hand-set timestamp does not name that commit's "
        f"time, so the unconditional half would be checking a number somebody typed")


def test_the_artifact_declares_the_graphs_it_leaves_behind():
    """A run creating N graphs and dropping none must say so — a reader sizing the
    lane's footprint needs the number, and `test_lane.py down` is the disposal."""
    art = _artifact()
    cleanup = art["cleanup"]
    assert cleanup["graphs_dropped"] == 0
    assert cleanup["graphs_created"] > 0
    assert "POPULATED" in cleanup["note"]


def test_the_bundle_does_not_restate_m2s_numbers():
    """⛔ The boundary between the two measurements, held mechanically.

    M2 measured retrieval QUALITY at a window. M5 measured the MISS RATE. Quoting
    M2's recall figures inside M5's bundle would let a reader take a quality drop
    for a miss rate — the conflation that made these two separate issues.
    """
    text = README.read_text()
    for forbidden in ("88.3", "46.0 %", "recall@3 falls"):
        assert forbidden not in text, (
            f"the M5 bundle quotes M2's quality figure {forbidden!r}; M5 measures "
            f"the miss rate and must cite M2 rather than restate it")


# ── 3b. the RECORD's tables, CELL BY CELL at the row/column they print under ──
# ⛔ These replace a NON-EMPTY-INTERSECTION check (#14162 F4) that was green
# while the record printed `20.8 %` for a cell the artifact holds as `20.4 %`, and
# stayed green with every non-zero percentage replaced by `44.4 %` — while being
# HOSTILE to a correct doc, because §3's doubling gains are not in
# `labels.*.windows`. Membership is NOT the property: the `:Point` peak (`24.6 pp`)
# is a real artifact value and is still WRONG under the `0.20→0.40` column, and the
# sensitivity table's mass column is real for `:Point` and wrong for `:Object`.
# So each table below is recomputed from the artifact and compared cell by cell.

def _num(value: float, places: int = 1) -> str:
    """Render a number at `places`, halves UP — the record's own convention.

    `round()` (banker's) would render 82.5 as 82 and 2036.5 as 2036; the record
    prints 83 and 2037, and it printed 82 for 82.5 before this pin existed.
    """
    quantum = Decimal(1).scaleb(-places)
    return f"{Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP):.{places}f}"


def _pct(rate: float) -> str:
    """A rate as the record prints it — `0 %` when exactly zero."""
    return "0 %" if rate == 0 else f"{_num(rate * 100)} %"


def _table(text: str, header_prefix: str, after: str | None = None):
    """(header, [(label, [cells…])]) for the markdown table at `header_prefix`.

    `after` disambiguates the two §4 tables, which share a header row. Bolding is
    stripped from every cell, so the pin holds the FIGURES and the row/column they
    sit in, not the typography.
    """
    lines = text.splitlines()
    floor = next((i for i, ln in enumerate(lines) if after in ln), 0) if after else 0
    start = next((i for i, ln in enumerate(lines)
                  if i >= floor and ln.startswith(header_prefix)), None)
    assert start is not None, f"no table starting {header_prefix!r} (after {after!r})"
    header = [c.strip().replace("**", "") for c in lines[start].strip("|").split("|")]
    rows, i = [], start + 2
    while i < len(lines) and lines[i].startswith("|"):
        cells = [c.strip().replace("**", "") for c in lines[i].strip("|").split("|")]
        assert len(cells) == len(header), (
            f"{header_prefix!r}: row {i + 1} has {len(cells)} cells, its header has {len(header)}")
        rows.append((cells[0], cells[1:]))
        i += 1
    return header, rows


def _t_verdict(art, header):
    per = {k: art["labels"][k]["largest_tenant_mb"]["per_window"]["0.10"]
           for k in ("object", "point")}
    return [(name, [f"{_num(per[k]['solo'], 0)} MB", f"{_num(per[k]['team'], 0)} MB"])
            for k, name in (("object", "`:Object`"), ("point", "`:Point`"))]


def _t_curve(art, header):
    coverages = [float(cell) for cell in header[1:]]
    out = []
    for key, name in (("object", "`:Object`"), ("point", "`:Point`")):
        by_cov = {round(row["coverage"], 4): row["fault_in_rate"]
                  for row in art["labels"][key]["curve"]}
        out.append((name + " fault-in", [_pct(by_cov[round(cov, 4)]) for cov in coverages]))
    return out


def _t_doubling(art, header):
    """Every printed interval must be one the artifact COMPUTED — never a value
    carried into a column whose interval is a different one (#14162 F2)."""
    intervals = [tuple(float(x) for x in cell.split("→")) for cell in header[1:]]
    out = []
    for key, name in (("object", "`:Object`"), ("point", "`:Point`")):
        by_interval = {(round(g["from_coverage"], 4), round(g["to_coverage"], 4)): g["pp_removed"]
                       for g in art["labels"][key]["flattening"]["scale_free"]["gains"]}
        out.append((name + " pp removed",
                    [_num(by_interval[(round(a, 4), round(b, 4))]) for a, b in intervals]))
    return out


def _windows(key):
    def build(art, header):
        rows = []
        for size in SIZES_MB:
            cells = []
            for tier in TIERS:
                cell = art["labels"][key]["windows"][str(size)][tier]
                cells.append("0 % (fully resident)" if cell["coverage"] == 1.0 else
                             f"{_pct(cell['fault_in_rate'])} (F = {_num(cell['coverage'], 2)})")
            label = f"{size} MB" + (" (§8's reference)" if size == 150 else "")
            rows.append((label, cells))
        return rows
    return build


def _t_bars(art, header):
    rows = []
    for bar in MISS_BARS:
        per = {k: art["labels"][k]["largest_tenant_mb"]["per_window"][f"{bar:.2f}"]
               for k in ("object", "point")}
        cells = [f"{_num(per[k][tier], 0)} MB"
                 for k in ("object", "point") for tier in ("solo", "team")]
        rows.append((f"≤ {_num(bar * 100, 0)} %", cells))
    return rows


def _t_sensitivity(art, header):
    """TWO mass columns (#14162 F3).

    `zipf_masses` normalises by population size, so the same exponent over 711
    `:Point` nodes and 1000 `:Object` nodes is NOT the same curve — the record
    printed `:Point`'s column under a sentence claiming it covered both. The
    flattening column is printed once, so both labels must agree first.
    """
    notes = {"0.6": " (flatter than `:Point`'s fit)", "1.2": " (steeper than either)"}
    rows = []
    for exponent in SENSITIVITY_EXPONENTS:
        key = f"{exponent:.1f}"
        cells = [_pct(art["labels"][label]["sensitivity"]["rows"][key]
                      ["fitted_curve_covered_mass"]["0.1"])
                 for label in ("point", "object")]
        flat = {label: art["labels"][label]["sensitivity"]["rows"][key]["flattening_at_150mb"]
                for label in ("point", "object")}
        widths = {(block or {}).get("w_mb") for block in flat.values()}
        assert len(widths) == 1, f"{key}: the labels' flattening column disagrees — {flat}"
        width = widths.pop()
        cells.append("never" if width is None else f"{_num(width)} MB")
        rows.append((key + notes.get(key, ""), cells))
    rows.append(("observed", [
        f"{_pct(art['labels']['point']['mass']['top_10pct_share'])}"
        f" (fit {_num(art['labels']['point']['mass']['fitted_zipf_exponent'], 2)})",
        f"{_pct(art['labels']['object']['mass']['top_10pct_share'])}"
        f" (fit {_num(art['labels']['object']['mass']['fitted_zipf_exponent'], 2)})",
        "see §5",
    ]))
    return rows


def _t_control(art, header):
    rows = []
    for label, key in (("0.75", "0.75"), ("0.50", "0.5"), ("0.25", "0.25")):
        cells = [_pct(art["labels"][lab]["arms"][f"mass@{key}|{weight}"]["fault_in_rate"])
                 for lab in ("object", "point") for weight in ("mass", "uniform")]
        rows.append((label, cells))
    return rows


def _t_policy(art, header):
    rows = []
    for label, key in (("0.75", "0.75"), ("0.50", "0.5"), ("0.25", "0.25")):
        cells = []
        for lab in ("object", "point"):
            delta = art["labels"][lab]["policy_delta"][key]
            cells += [_pct(delta["fault_in_mass_ordered"]),
                      _pct(delta["fault_in_random_ordered"]),
                      f"{_num(delta['pp_removed_by_usage_ordering'])} pp"]
        rows.append((label, cells))
    return rows


def _t_floor(art, header):
    """The M2 column is M2's figure, cited rather than restated, and pinned by
    M2's own suite — it is the one cell here this artifact does not hold."""
    m2_cell = {"object": "ceiling 1.0", "point": "28.7 %"}
    rows = []
    for key, name in (("object", "`:Object`"), ("point", "`:Point`")):
        block = art["labels"][key]
        rows.append((name, [
            f"{_num(block['floor']['miss_resident'] * 100)} %",
            f"{_num(block['arms']['complete|uniform']['miss_resident'] * 100)} %",
            m2_cell[key],
        ]))
    return rows


# name -> (header prefix, `after` marker, builder). The §4 pair shares its header,
# so each is anchored under its own label's heading.
_TABLES = {
    "verdict-largest-tenant": ("| | solo (77 MB) | team (615 MB) |", None, _t_verdict),
    "curve": ("| coverage `F` | 0.02 |", None, _t_curve),
    "doubling": ("| doubling | 0.05→0.10 |", None, _t_doubling),
    "windows-object": ("| tenant graph `S` | **solo** (77 MB) |", "### `:Object` — the entity dictionary", _windows("object")),
    "windows-point": ("| tenant graph `S` | **solo** (77 MB) |", "### `:Point` — the claim population", _windows("point")),
    "miss-bars": ("| miss bar | `:Object` — solo |", None, _t_bars),
    "sensitivity": ("| exponent | mass at top 10 % — `:Point` |", None, _t_sensitivity),
    "uniform-control": ("| coverage | `:Object` mass reads |", None, _t_control),
    "policy": ("| coverage | `:Object` usage |", None, _t_policy),
    "floor": ("| label | complete graph, mass reads |", None, _t_floor),
}


@pytest.mark.parametrize("name", sorted(_TABLES))
def test_every_readme_table_cell_is_recomputed_from_the_artifact(name):
    """Every cell of every published table, at the row and column it sits in.

    Cell EQUALITY, not membership: a figure the artifact holds at a different
    coverage (the `:Point` peak printed in the `0.20→0.40` column) or for the other
    label (the sensitivity table's mass column) reds here.
    """
    header_prefix, after, build = _TABLES[name]
    header, rows = _table(README.read_text(), header_prefix, after)
    expected = build(_artifact(), header)
    assert [row[0] for row in rows] == [row[0] for row in expected], (
        f"{name}: the record's rows {[r[0] for r in rows]} != the artifact's {[r[0] for r in expected]}")
    for (label, cells), (_, want) in zip(rows, expected, strict=True):
        for column, got, exp in zip(header[1:], cells, want, strict=True):
            assert got == exp, f"{name}/{label}/{column}: record prints {got!r}, artifact gives {exp!r}"


# The one percentage in the record that is NOT this artifact's: M2's found@deep on
# the complete `:Point` graph, quoted as the cross-check it is. Restating M2's
# figures as M5's is the conflation the two issues exist to separate, so it stays a
# citation and is pinned by M2's own suite.
_NON_ARTIFACT_FIGURES = {"28.7"}


def _artifact_percentages(art) -> set[str]:
    """Every float the artifact holds, rendered as a percentage at one decimal."""
    out: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            out.add(f"{node * 100:.1f}")
            out.add(f"{abs(node) * 100:.1f}")
            out.add(_num(node * 100))
            out.add(_num(abs(node) * 100))

    for key in ("labels", "uniform_query_control", "full_corpus_curves"):
        walk(art[key])
    return out


def test_no_record_percentage_is_outside_the_artifact():
    """The membership half — a figure that appears NOWHERE in the artifact.

    Weaker than the table pin above (a value can be real and misplaced), which is
    why BOTH run: this one is what catches a stale prose figure, and it reds on the
    `20.8 %` this round found before any table rule would.
    """
    allowed = _artifact_percentages(_artifact()) | _NON_ARTIFACT_FIGURES
    text = README.read_text()
    for figure in sorted(set(re.findall(r"(\d+\.\d) %", text))):
        assert figure in allowed, (
            f"the record states {figure} % and the artifact holds no such value — "
            f"the doc and the evidence have drifted")


# The one `pp` figure no run of this instrument's artifact can hold: §10's
# run-to-run reproducibility band, which needs TWO re-runs to measure and is
# recorded in prose for that reason ("The one figure that CANNOT be recomputed").
_CROSS_RUN_FIGURES = {"0.4"}


def _provenance_cells() -> dict[str, str]:
    """The Provenance block's `key -> value` cells, in file order."""
    lines = README.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "## Provenance")
    out: dict[str, str] = {}
    for ln in lines[start:]:
        if ln.startswith("## ") and ln.strip() != "## Provenance":
            break
        if not ln.startswith("|") or "---" in ln:
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) == 2 and cells[0]:
            out[cells[0]] = cells[1]
    return out


def test_the_provenance_cells_are_the_artifacts():
    """The Provenance block's counts, cell by cell (#14162 review F2).

    The ten FIGURE tables are recomputed by the pin above; this block is keyed rather
    than tabular, so it was the one table above §10 that nothing read — and its counts
    are EXACTLY the ones this record says move between runs on a live corpus
    (`definite` names 156,106 -> 156,148 observed; the artifact now holds 156,510).
    Without this pin the next re-cut leaves a stale `156,510` beside an artifact that
    does not hold it, with every other pin green — the drift class this PR closes.
    """
    art = _artifact()
    c = art["corpus"]
    cells = _provenance_cells()

    def nums(key: str) -> list[str]:
        # `\d[\d,]*` — a comma is only part of a number when a digit precedes it;
        # the bare `,` separators in these sentences are not cells.
        return re.findall(r"\d[\d,]*", cells[key])

    # corpus: files / lines / parse errors, in the order the sentence states them.
    assert nums("corpus") == [f"{c['corpus']['files']:,}", f"{c['corpus']['lines']:,}",
                              f"{c['corpus']['parse_errors']:,}"], (
        f"Provenance/corpus prints {nums('corpus')}, artifact holds "
        f"{[c['corpus']['files'], c['corpus']['lines'], c['corpus']['parse_errors']]}")

    # populations: the recorded funnel, then `:Object`'s node count.
    b = c["point_build"]
    funnel = [b["built"], b["requested"], b["too_short"], b["no_span"],
              b["sanitised_out"], b["duplicate_span"]]
    assert [int(n.replace(",", "")) for n in nums("populations")[:6]] == funnel, (
        f"Provenance/populations prints {nums('populations')[:6]}, artifact funnel is {funnel}")
    assert int(nums("populations")[6].replace(",", "")) == art["labels"]["object"]["nodes"], (
        f"Provenance/populations states :Object {nums('populations')[6]}, "
        f"artifact built {art['labels']['object']['nodes']}")

    # harvest: the three names_harvested counts.
    h = c["names_harvested"]
    assert nums("harvest") == [f"{h['definite']:,}", f"{h['ident']:,}", f"{h['path']:,}"], (
        f"Provenance/harvest prints {nums('harvest')}, artifact holds "
        f"{[h['definite'], h['ident'], h['path']]}")

    # windows: the SHIPPED values, which this instrument reads and never sets.
    w = art["windows"]["mb"]
    assert nums("windows") == [str(w["solo"]), str(w["pro"]), str(w["team"])], (
        f"Provenance/windows prints {nums('windows')}, artifact read "
        f"{[w['solo'], w['pro'], w['team']]}")


def test_the_architecture_s_9_2_table_is_recomputed_from_the_artifact():
    """§9.2's M5 table, cell by cell (#14162 review F3).

    `SEARCH-ARCHITECTURE.md` §9.2 is the design input `#13989` sizes an allowance
    from, and M2's sibling §9.1 is pinned cell by cell by
    `tests/test_window_quality_m2.py`. M5's equivalent read only the README, so §9.2
    could drift alone: mutating `20.9 % / 25.9 %` to `21.9 % / 26.9 %`, or
    `≈149 / ≈184` to `≈159 / ≈194`, left every pin GREEN before this one existed.

    The cells are recomputed with the SAME two functions the record used —
    `coverage_for` (the window over the tenant size) and `_interp` (the curve at that
    coverage) — so the pin holds the METHOD, not just the value.
    """
    from tools.miss_rate_m5 import _interp, coverage_for

    art = _artifact()
    text = ARCH.read_text()
    header, rows = _table(text, "| tenant graph `S` |", after="§9.2")
    assert header[1:] == ["solo (77 MB)", "pro (102 MB)", "team (615 MB)"], header

    windows = [art["windows"]["mb"][k] for k in ("solo", "pro", "team")]
    sizes = [150, 300, 600]
    assert [row[0] for row in rows] == [
        ["150 MB (§8's reference estimate)", "300 MB", "600 MB"][i] for i in range(3)]

    for (_label, cells), size_mb in zip(rows, sizes, strict=True):
        for window_mb, cell in zip(windows, cells, strict=True):
            want = [_pct(_interp(art["labels"][k]["curve"],
                                 coverage_for(window_mb, size_mb)))
                    for k in ("object", "point")]
            # `c.strip()` FIRST: the cells around `/` carry padding, and a trailing
            # space would stop the `$`-anchored label strip from matching cell 1.
            got = [re.sub(r"\s*`:\w+`\s*$", "", c.strip()).strip() for c in cell.split("/")]
            assert got == want, (
                f"§9.2/S={size_mb}/W={window_mb}: the doc prints {got}, the artifact gives "
                f"{want} (`:Object` / `:Point`)")

    # The covered-tenant sentence under the table, from the same artifact block.
    per = {k: art["labels"][k]["largest_tenant_mb"]["per_window"]["0.10"]
           for k in ("object", "point")}
    for needle in (f"≈{_num(per['object']['solo'], 0)} MB", f"≈{_num(per['point']['solo'], 0)} MB",
                   f"≈{_num(per['object']['team'] / 1000, 2)} GB",
                   f"≈{_num(per['point']['team'] / 1000, 2)} GB"):
        assert needle in text, (
            f"§9.2 states no {needle!r}; the artifact's largest covered tenant is "
            f"{per['object']['solo']}/{per['point']['solo']} MB solo and "
            f"{per['object']['team']}/{per['point']['team']} MB team")


def test_no_record_pp_figure_is_outside_the_artifact():
    """The `pp` half: doublings and policy deltas are the only two sources."""
    art = _artifact()
    allowed = set(_CROSS_RUN_FIGURES)
    for key in ("object", "point"):
        for gain in art["labels"][key]["flattening"]["scale_free"]["gains"]:
            allowed.add(_num(gain["pp_removed"]))
        for delta in art["labels"][key]["policy_delta"].values():
            allowed.add(_num(delta["pp_removed_by_usage_ordering"]))
    text = README.read_text()
    # `0.1 pp/MB` is a RATE, not a pp figure — the negative lookahead keeps it out.
    for figure in sorted(set(re.findall(r"(\d+\.\d) pp(?![/\w])", text))):
        assert figure in allowed, (
            f"the record states {figure} pp and no artifact gain or delta holds it")


def test_the_record_attributes_the_point_peak_to_the_interval_the_artifact_holds():
    """⛔ Placement, for prose (#14162 F2).

    The peak (`24.6 pp`) is a value the artifact holds — membership cannot see that
    it belongs to the `0.15→0.30` doubling, and the record (plus both document
    copies) attributed it to `F = 0.10` and to `F = 0.20`. Every sentence that
    quotes the peak must name the interval the artifact computed it over.
    """
    art = _artifact()
    peak = max(art["labels"]["point"]["flattening"]["scale_free"]["gains"],
               key=lambda gain: gain["pp_removed"])
    interval = f"{_num(peak['from_coverage'], 2)}→{_num(peak['to_coverage'], 2)}"
    figure = f"{_num(peak['pp_removed'])} pp"
    seen = 0
    for path in (README, ARCH, ARCH_CHANGELOG):
        text = " ".join(path.read_text().split())
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if figure in sentence:
                seen += 1
                assert interval in sentence, (
                    f"{path.name}: a sentence quotes the :Point peak {figure} without "
                    f"naming the {interval} doubling it was computed over: {sentence!r}")
    assert seen, f"no document quotes the :Point peak ({figure}) any more — this pin has lost its subject"


def test_the_artifact_is_not_m2s_artifact_under_a_new_name():
    """A guard against the cheapest possible failure: publishing M2's artifact."""
    art = _artifact()
    m2 = json.loads(M2_ARTIFACT.read_text())
    assert art["measurement"] == "M5" and m2["measurement"] == "M2"
    assert art["issue"] == 14015 and m2["issue"] == 12186
    assert art["provenance"]["tool_sha256"] != m2["provenance"]["tool_sha256"]

# ── 1b. INVARIANT 1's cross-check: the guards FIRING (#14162 F3/F5) ──────────
# ⛔ These live at module level in the instrument precisely so they can be driven
# here. The guard they replace — `fault_in_rate == 1 - resident_query_share` — was
# algebra over two complementary slices of one list and could not fail under ANY
# mutation, while the docstring sold it as a sampler/residency detector.

POP = {f"pt-14015{i:06d}" for i in range(5)}


def test_invariant_1b_refuses_a_leg_row_with_no_id():
    """The F5 FAIL-OPEN: an unidentifiable row is not a miss.

    When the leg's rows stop carrying `id` under that key (a rename, a wrapper, an
    adapter), `[r.get("id") for r in rows]` yields `[None, None, ...]`, every query
    reads as a miss, `empty_rate` stays 0.0 because the list is non-empty, and the
    pre-fix tree published `floor.miss_resident = 1.0` — "no window can buy a miss
    rate below 100 %" — with INVARIANTS 1/2/3/4 all green.
    """
    with pytest.raises(RuntimeError, match="not a measurement"):
        check_leg_ids("point", "pt-14015000000", [None, None], POP)


def test_invariant_1b_refuses_an_id_from_outside_the_population():
    """A returned node the instrument never built is a different graph's read."""
    with pytest.raises(RuntimeError, match="outside the population"):
        check_leg_ids("point", "pt-14015000000", ["pt-14015000000", "pt-9999"], POP)


def test_invariant_1b_is_bounded_on_the_legitimate_cases():
    """⛔ A guard that fires on everything is not a guard."""
    check_leg_ids("point", "pt-14015000000", ["pt-14015000001"], POP)
    check_leg_ids("point", "pt-14015000000", [], POP)  # an empty read IS a miss


def test_invariant_1a_refuses_a_query_that_names_a_node_outside_the_population():
    """The sampler<->population cross-check that replaced the dead identity."""
    with pytest.raises(RuntimeError, match="outside"):
        check_query_ids("point", [{"id": "pt-14015000000"}, {"id": "pt-ghost"}], POP)


def test_invariant_1a_accepts_a_sampled_query_set():
    check_query_ids("point", [{"id": i} for i in sorted(POP)], POP)


def test_invariant_1c_refuses_a_complete_arm_that_missed_every_query():
    """"every query missed a graph holding every node" is broken, not measured."""
    with pytest.raises(RuntimeError, match="COMPLETE arm"):
        check_arm("point", 250, 1.0, set(POP), POP)


def test_invariant_1c_does_not_fire_on_a_real_floor():
    """⛔ BOUNDED ON PURPOSE: M2 measured 28.7 % found-deep on a COMPLETE `:Point`
    graph, and this instrument's own artifact records 22.4 %. Only the TOTAL-miss
    reading is impossible; a guard that refused any high floor would refuse the
    measurement this instrument exists to make."""
    check_arm("point", 250, 1.0 - 0.7133, set(POP), POP)  # M2's found@deep ceiling
    check_arm("point", 250, 0.287, set(POP), POP)        # this run's floor


def test_invariant_1c_does_not_fire_on_an_evicted_arm():
    """An EVICTED arm missing every query is the window's ANSWER, not a defect."""
    check_arm("point", 250, 1.0, set(POP) - {"pt-14015000004"}, POP)


def test_the_dead_identity_is_gone_and_the_cross_check_is_wired():
    """⛔ The WIRING pin (#14162 F3). A guard the instrument never calls is dead.

    F3's finding was that `measure()` asserted an ALGEBRAIC identity — both sides
    counts over complementary slices of one `rows` list — so no mutation could make
    it raise, while the docstring sold it as a sampler/residency detector. This
    reds if either the identity comes back or a replacement guard is left unwired
    inside the closure, which no live-graph test could observe.
    """
    source = TOOL.read_text()
    assert "fault_in_rate - (1.0 - out" not in source, (
        "the tautological INVARIANT 1 is back — it is algebra over two "
        "complementary slices of the same list and cannot fail under any mutation")

    tree = ast.parse(source)
    measure = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef) and node.name == "measure")
    called = {node.func.id for node in ast.walk(measure)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert {"check_query_ids", "check_leg_ids", "check_arm"} <= called, (
        f"INVARIANT 1's guards are not all wired into measure(): called={sorted(called)}")


# ── 1c. one spelling of "a term", on BOTH sides of the match (#14162 F4) ────

def test_terms_of_strips_the_same_characters_from_both_sides():
    """Punctuation cannot make the same word two different terms."""
    assert terms_of('first. INTERNAL: "error"') == {"first", "INTERNAL", "error"}
    assert terms_of("first") == terms_of("first.") == terms_of("(first)")


def test_terms_of_drops_tokens_below_the_length_floor():
    assert terms_of("a an of") == set()
    assert all(len(t) >= TERM_MIN_LEN for t in terms_of("ab abc abcd"))


def test_span_mass_normalises_the_span_with_the_shared_helper():
    """⛔ The TOOL's own `_span_mass`, not a spelling re-implemented here.

    Two units, a fully punctuated span. Under the PRE-FIX code the span's terms
    were `alpha.`/`beta.`/`gamma.`, which intersect a stripped unit in NOTHING, so
    the majority test fails and `_span_mass` clamps to 1 — the undercount F4
    records. A test that rebuilt the normaliser itself would pass over exactly that
    defect, so this one calls the instrument.
    """
    units = [terms_of("alpha beta gamma"), terms_of("alpha beta gamma")]
    assert _span_mass("alpha. beta. gamma.", units) == 2


def test_the_pre_fix_spelling_finds_no_match_on_the_same_corpus():
    """⛔ The defect asserted AS a failing shape, so it cannot be reintroduced.

    `row["query"]` is by construction the RAW token run from `query_span`, so
    punctuated spans are the normal case. Under the pre-fix code the span was split
    without stripping while the corpus units WERE stripped — `first.` never matched
    `first` for the same word in the same corpus. The bias had a knowable
    direction: a flatter `:Point` mass puts the curve closer to the diagonal, i.e.
    fewer/no flattening intervals, i.e. the null "this curve never flattens" —
    which is the answer `#13989` would then quote.
    """
    corpus = ['The window demoted a point. INTERNAL: "error" seen.']
    units = [terms_of(t) for t in corpus]
    assert _span_mass('point. INTERNAL: "error"', units) == 1
    pre_fix_terms = {t for t in ["point.", "INTERNAL:", '"error"'] if len(t) >= 3}
    assert pre_fix_terms & units[0] == set(), (
        "the pre-fix (unstripped) span spelling is expected to miss every term — "
        "if it matches, this pin has stopped describing the defect")


def test_run_builds_the_corpus_units_with_the_same_helper():
    """⛔ BOTH SIDES of the match, or the normaliser only fixes half of it."""
    source = TOOL.read_text()
    assert "turn_terms = [terms_of(t) for t in texts]" in source, (
        "the corpus units are no longer built by the shared `terms_of` — the span "
        "side and the unit side can drift apart again (#14162 F4)")
    assert "set(t.split())" not in source, (
        "a raw `set(t.split())` is back in the tool: that is the unstripped "
        "spelling the recurrence proxy must never use on either side")


def test_the_recurrence_proxy_counts_matching_corpus_units():
    """"in how many units of the corpus does this identity recur" — the definition."""
    units = [terms_of("alpha beta gamma"), terms_of("alpha beta delta"),
             terms_of("zeta eta theta")]
    assert _span_mass("alpha beta", units) == 2
    assert _span_mass("alpha upsilon", units, majority=False) == 2
    assert _span_mass("a b", units) == 1, "no usable terms is not 'maximally popular'"


def test_the_term_strip_set_is_the_same_one_the_normaliser_uses():
    """One definition of "strippable", so the two cannot drift apart again."""
    assert set(TERM_STRIP) == set(".,;:!?()[]{}\"'`")
    assert terms_of("hello.") == {"hello"}


# ── 4. the declared absence of the evidence bundle, and its TRIPWIRE ────────

def test_the_committed_bundle_makes_the_artifact_backed_pins_run():
    """⛔ The tripwire's INVERSE, and the reason the declared absence is gone.

    `ce0bf1bf` added this file ahead of the artifact its own message says lands in
    the NEXT commit, which left 14 cases raising `FileNotFoundError` — a control
    that cannot run (`#14162` F2's class). The bundle is committed now, so the
    guard was removed rather than left to outlive its reason, and this case holds
    the removal: the artifact exists AND the pins that need it are running rather
    than skipping. If the bundle is ever withdrawn, this reds instead of the pins
    quietly going back to sleep.
    """
    assert ARTIFACT.exists(), (
        f"{ARTIFACT.relative_to(ROOT)} is gone — the artifact-backed pins below "
        f"cannot run, and a bundle cannot be withdrawn without this file saying so")
    art = _artifact()
    assert art["measurement"] == "M5" and art["issue"] == 14015
    assert art["labels"], "the bundle carries no label blocks"
# ── 1d. the scale-free marginal: a DOUBLING, not a megabyte ─────────────────

def test_a_doubling_is_the_scale_free_marginal_and_needs_no_tenant_size():
    """⛔ `pp/MB` is S-DEPENDENT by construction (`W = F x S`), so the knee it finds
    moves with the tenant; a doubling is dimensionless. Pinned as a SIGNATURE fact
    because that is the whole argument for the primary reading."""
    import inspect
    assert set(inspect.signature(doubling_gain).parameters) == {"curve", "grid"}
    rows = doubling_gain(lorenz(sorted(zipf_masses(400, 1.2), reverse=True)))
    assert rows, "a curve with >= 2 points has doublings"
    for row in rows:
        assert row["to_coverage"] == pytest.approx(row["from_coverage"] * 2, abs=1e-9)
        assert row["pp_removed"] > 0, "twice the window cannot remove LESS miss rate"
    # Diminishing returns: the first doubling buys more than the last.
    gains = [r["pp_removed"] for r in rows]
    assert gains == sorted(gains, reverse=True), gains


def test_the_scale_free_knee_is_none_for_a_curve_with_no_knee():
    """⛔ A constant pp per doubling IS a log-linear curve with no knee, and
    reporting one would invent it — the `:Object` shape the record calls out."""
    uniform = [{"coverage": f, "fault_in_rate": 1.0 - f} for f in CURVE_COVERAGES]
    assert flattening_coverage(uniform) is None
    # Exponent 1.8 is where the LAST doubling on the declared grid (0.50 -> 1.00)
    # finally removes under FLAT_PP_PER_DOUBLING; at 1.2 the largest doubling still
    # buys 5.2 pp, so the grid ends before any knee — also a real answer.
    steep = lorenz(sorted(zipf_masses(500, 1.8), reverse=True))
    knee = flattening_coverage(steep)
    assert knee is not None
    assert knee["coverage"] > 0.0
    assert knee["criterion"].endswith("of fault-in rate")


def test_largest_tenant_mb_inverts_coverage_for_miss():
    """`S = W/F` — the number `#13989` needs is a tenant SIZE, not a rate.
    Checked as an INVERSION of its own companion, so the two cannot disagree."""
    curve = lorenz(sorted(zipf_masses(800, 1.2), reverse=True))
    for bar in (0.20, 0.10, 0.05):
        f = coverage_for_miss(curve, bar)
        assert f is not None and f > 0.0
        assert largest_tenant_mb(curve, 77, bar) == pytest.approx(round(77 / f, 1))
    # A stricter bar fits a SMALLER tenant.
    assert largest_tenant_mb(curve, 77, 0.20) > largest_tenant_mb(curve, 77, 0.05)
    # A larger window fits a LARGER one.
    assert largest_tenant_mb(curve, 615, 0.10) > largest_tenant_mb(curve, 77, 0.10)
    # An unreachable bar is `None`, never a fabricated 0 or an infinity.
    assert largest_tenant_mb(curve, 77, -0.01) is None


def test_coverage_for_miss_reports_the_smallest_covering_grid_point():
    """Read off the grid it names, so a rate is never presented at a precision the
    grid does not carry."""
    curve = lorenz(sorted(zipf_masses(400, 1.2), reverse=True))
    assert coverage_for_miss(curve, -0.01) is None
    full = coverage_for_miss(curve, 1.0)
    assert full is not None and full > 0.0, "everything misses nothing only at F=1"
