"""Pins M5's arithmetic, its invariants, and the artifact its record cites.

The evidence record (`docs/evidence/14015-m5-miss-rate/README.md`) publishes
numbers `#13989`'s allowance sizing will quote, and they sit on an instrument
whose failure mode is SILENT — the curve is arithmetic on a mass distribution, so
an inverted ordering, a sampler seeded from the same stream as the eviction order,
or a curve read off the wrong population still produces a table of plausible
rates. Four things are pinned here, each closing a different way the record rots:

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
4. **The read the figures came from.** ``query()`` calls itself a MIRROR of
   ``extractor_v2._fts_rows``, which is the ground the rerank census grants it, so
   the two call sites are compared keyword by keyword against each other (§5) —
   including the derived flags a literal-only comparison silently skips — and the
   width the probe measures at is held at or above the pool the granted lever would
   re-pick.

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
from tortoise.rerank import (  # noqa: E402
    DEFAULT_FUSED_RERANK_POOL,
    fused_rerank_pool_width,
    rerank_pool_enabled,
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
    """INVARIANT 3, plus a hand-edit cross-check the module header says is NOT an invariant.

    ``miss == fault_in_rate + miss_resident × resident_query_share`` is the split a reader
    reads, and ``fault_in_rate == 1 - resident_query_share`` is the identity the STORED bundle
    must satisfy: both halves are written by the instrument, so a HAND-EDIT of one of them
    drifts the other. ⛔ It is deliberately NOT a sampler or residency detector — the two
    values are complementary slices of one list, so no mutation of the sampler can make this
    fire, and the module header's "do not re-add this as an invariant" applies to exactly that
    claim. Kept for the hand-edit class, which it does catch.
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

    ⛔ **The never-later half is checked WITHOUT git, on purpose** (#14162 review round 4):
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
    # ⛔ PROXIMITY, not membership (#14162 review round 4): the README prints the
    # ANCHOR in the identical backtick form (`**git head:** \`3343599d9847\``), so a
    # bare substring test is satisfied by setting `run_git_head := git_head` while
    # the record still never mentions the run — measured GREEN in the CI shape. The
    # run head must appear ALONGSIDE the field name that makes it the run side.
    # ⛔ `[\s\S]`, not `[^\n]`: a pure RE-WRAP of a correct record must not red
    # this pin (measured: the two tokens sit 18 chars apart on one line, and the
    # line is already near the doc's wrap width, so `[^\n]` is a false block
    # waiting to happen — while `[\s\S]` still rejects the anchor substitution,
    # whose nearest `provenance.run_git_head` is far beyond the window).
    text = README.read_text()
    assert re.search(rf"`{run_head[:12]}`[\s\S]{{0,80}}provenance\.run_git_head", text), (
        f"the record does not present `{run_head[:12]}` AS `provenance.run_git_head` — "
        f"naming it anywhere (the anchor is printed in the same form) is not enough, "
        f"and that substitution is what this pin exists to forbid")

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


def _pp_terms(text: str) -> set[str]:
    r"""Every `pp` figure a text states — including a RANGE's lower bound.

    ⛔ `re.findall(r"(\d+\.\d) pp")` can only see the number immediately before
    ` pp`, so in `34.8 – 36.0 pp` the `34.8` was unguarded: wronging both range
    lower bounds in the README, §9.2 and the changelog left the suite green
    (#14162 round 4; measured GREEN on 7 mutants). A range states TWO figures and
    both are claims about the artifact.
    """
    terms = set(re.findall(r"(\d+\.\d) pp(?![/\w])", text))
    for lower, upper in re.findall(r"(\d+\.\d) ?[–-] ?(\d+\.\d) pp(?![/\w])", text):
        terms.update((lower, upper))
    return terms


def _section(text: str, start: str, end_prefix: str = "## ") -> str:
    """One heading's slice — heading line to the next heading of that level."""
    lines = text.splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.startswith(start)), None)
    assert at is not None, f"no heading starting {start!r} — this pin has lost its subject"
    for i in range(at + 1, len(lines)):
        if lines[i].startswith(end_prefix):
            return "\n".join(lines[at:i])
    return "\n".join(lines[at:])


def _m5_documents():
    """Every surface that restates the record's figures, sliced to M5's own text.

    ⛔ The two documents were read by ONE assertion (the `:Point` peak's interval),
    so §9.2's prose and the changelog's pp figures could be mutated with the whole
    suite green — and `tests/test_window_quality_m2.py`'s scan was (correctly)
    bounded before §9.2, so nothing else reached them. `#14196`'s re-cut moves
    exactly these arm-derived figures: the README would red and these two would not.
    """
    return [
        ("README.md", README.read_text()),
        ("SEARCH-ARCHITECTURE.md §9.2", _section(ARCH.read_text(), "### 9.2 ", end_prefix="### ")),
        ("SEARCH-ARCHITECTURE-CHANGELOG.md `#14015`",
         _section(ARCH_CHANGELOG.read_text(), "## `#14015`")),
    ]


def test_no_record_percentage_is_outside_the_artifact():
    """The membership half — a figure that appears NOWHERE in the artifact.

    Weaker than the table pin above (a value can be real and misplaced), which is
    why BOTH run: this one is what catches a stale prose figure, and it reds on the
    `20.8 %` this round found before any table rule would.
    """
    allowed = _artifact_percentages(_artifact()) | _NON_ARTIFACT_FIGURES
    for name, text in _m5_documents():
        for figure in sorted(set(re.findall(r"(\d+\.\d) %", text))):
            assert figure in allowed, (
                f"{name} states {figure} % and the artifact holds no such value — "
                f"the doc and the evidence have drifted")


# The one `pp` figure no run of this instrument's artifact can hold: §10's
# run-to-run reproducibility band, which needs TWO re-runs to measure and is
# recorded in prose for that reason (see §10, "Measured between two runs:").
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
    # `0.1 pp/MB` is a RATE, not a pp figure — the negative lookahead keeps it out.
    for name, text in _m5_documents():
        for figure in sorted(_pp_terms(text)):
            assert figure in allowed, (
                f"{name} states {figure} pp and no artifact gain or delta holds it")


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


def test_the_committed_curve_sits_above_the_identity_it_was_checked_against():
    """INVARIANT 5, re-asserted ON the committed bundle.

    `lorenz()` refuses a curve below the identity, and its identity side is
    `covered_mass([1] * n, F)` — which is exactly `F`. The bundle stores the
    coverage AND the covered mass of every curve point, so the property is
    checkable here: a bundle that violates it stays green no longer.

    ⛔ Why it was missing: the record claimed INVARIANT 5 could NOT be pinned on
    the artifact ("it is arithmetic inside `lorenz()`"). That was wrong — the
    operands are both in the bundle — so a mutant setting one curve point's
    `covered_mass` to 0.01 at `F = 0.02` (with the record's cell kept consistent)
    passed the whole suite (#14162 round 5). The bundle is what a reader quotes, so
    a hand-edited one must red rather than publish an inverted curve.
    """
    art = _artifact()
    for label in ("object", "point"):
        block = art["labels"][label]
        # ⛔ The identity is `covered_mass([1] * n, F)`, not `F`: `covered_mass` rounds the
        # retained node COUNT, so on a finite population the equal-mass vector can sit
        # slightly below `f` — the tool's own docstring says so. Computing it the same way
        # `lorenz()` does is what makes this the SAME property rather than a lookalike.
        identity_of = [1] * block["nodes"]
        for row in block["curve"]:
            identity = covered_mass(identity_of, row["coverage"])
            assert row["covered_mass"] + 1e-12 >= identity, (
                f"{label}: at coverage {row['coverage']} the curve covers only "
                f"{row['covered_mass']} of the mass, below the identity {identity} — the "
                f"comparison `lorenz()` refuses, so the ordering it was built from is inverted")
            assert row["fault_in_rate"] == pytest.approx(1.0 - row["covered_mass"], abs=1e-6), (
                f"{label}: fault_in_rate {row['fault_in_rate']} is not 1 - covered_mass "
                f"{row['covered_mass']}")


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
    graph, and this instrument's own artifact records 24.0 % (``labels.point.floor
    .miss_resident``). Only the TOTAL-miss reading is impossible; a guard that refused
    any high floor would refuse the measurement this instrument exists to make."""
    check_arm("point", 250, 1.0 - 0.7133, set(POP), POP)  # M2's found@deep ceiling
    check_arm("point", 250, 0.24, set(POP), POP)         # this run's floor, from the artifact


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


# ── the pins that guard the PROSE claims a cell pin cannot reach ──────────────

def test_the_object_doubling_range_excludes_the_first_doubling_it_names():
    """The `10.4 – 12.0 pp per doubling` range is scoped to AFTER the first step.

    ⛔ This pins the CLAIM, not the datum. §3's table pin already holds the `15.5` cell, so
    reverting §0's scoping to "at every size" left every other pin green (measured: 71/71 on
    `d5bc1df8`). The claim is mechanically checkable — the artifact holds the first doubling's
    gain and it lies OUTSIDE the printed range — so a sentence that prints the range without
    excluding that step is a sentence the artifact refutes.
    """
    art = _artifact()
    first = next(g for g in art["labels"]["object"]["flattening"]["scale_free"]["gains"]
                 if round(g["from_coverage"], 4) == 0.05 and round(g["to_coverage"], 4) == 0.1)
    lo, hi = 10.4, 12.0
    assert not (lo <= first["pp_removed"] <= hi), (
        "the premise moved: the first doubling no longer sits outside the range, so this pin "
        f"has lost its subject ({first['pp_removed']} pp vs {lo}–{hi})")
    for name, text in _m5_documents():
        # PARAGRAPH scope, not document scope: a scoped sentence elsewhere in the same file must
        # not license an unscoped one here (measured — a document-wide window kept the pin green
        # with §0's own scoping reverted).
        for para in text.split("\n\n"):
            # Whitespace-flattened: the record wraps at ~100 columns, so "per doubling" and the
            # scope word both straddle lines. Matching the raw paragraph skipped §0 entirely
            # (measured), which is how an unscoped sentence stayed invisible.
            flat = " ".join(para.split())
            if _num(lo) not in flat or _num(hi) not in flat or "per doubling" not in flat:
                continue
            para = flat
            assert "after the first" in para.lower(), (
                f"{name}: a paragraph prints the {lo}–{hi} pp per-doubling range without scoping "
                f"it to after the first doubling, which the artifact puts at {first['pp_removed']} pp "
                f"— the range is refuted as written: {para.strip()[:200]!r}")


# Tables the record publishes that are NOT figure tables, and so are not cell-recomputed:
# §1 is a table of DEFINITIONS and the Provenance block is metadata. Naming them here is what
# keeps the census below honest — a table that is neither registered in `_TABLES` nor listed
# here is unguarded, which is the hole this pin exists to close.
_NON_FIGURE_TABLES = {
    "| quantity | definition | what it decides |",
    "| | |",
}


def test_every_readme_table_is_registered_in_the_cell_pins():
    """The cell-recompute census is a CENSUS, not a list that only sees itself.

    `_TABLES` names ten tables and every one is recomputed cell by cell — but a table ADDED to
    the record and not registered here was invisible to every pin (measured on `d5bc1df8`:
    a new table carrying two real-but-transposed artifact values left 71/71 green, because the
    `%`/`pp` membership pins accept any figure the artifact holds SOMEWHERE). So the number of
    published tables is pinned, not just the ones we remembered.
    """
    text = README.read_text()
    lines = text.splitlines()
    seeds = sorted({ln for prefix, _after, _b in _TABLES.values() for ln in [prefix]})
    # A table's first line is a header row: it starts with `|` and the NEXT line is the `|---|`
    # rule. Counting those (not every `|` line) is what makes "a new table" visible.
    found = []
    for i, line in enumerate(lines[:-1]):
        if line.startswith("|") and set(lines[i + 1].replace("|", "").replace(" ", "")) <= {"-", ":"} \
                and lines[i + 1].startswith("|"):
            found.append(line)
    # `_TABLES`' first element is a header PREFIX (enough to find the table), while `found`
    # holds the whole header row — so the comparison is a prefix match, not equality.
    missing = [p for p in seeds if not any(h.startswith(p) for h in found)]
    assert not missing, (
        f"a table header `_TABLES` names is no longer in the record: {missing}")
    unregistered = [h for h in found
                    if not any(h.startswith(p) for p in seeds) and h not in _NON_FIGURE_TABLES]
    assert not unregistered, (
        "the record publishes table(s) the cell pins do not recompute — a figure there is "
        f"membership-only, so a real-but-wrong value passes: {unregistered}")


# ── 5. the read this probe measures: a MIRROR of `_fts_rows`, not a reader of its own ──

#: The names whose VALUES may differ between the mirror and its reference, because
#: they are the site's own COORDINATES rather than part of the leg's shape: the leg
#: selector (`kind` / `entity_type`), the width (`k` / `limit`) and the trace sink
#: the probe collects for its own accounting. Every OTHER name both sites hand over
#: must agree with `_fts_rows` in form, not merely in name.
_SITE_COORDINATES = frozenset({"entity_type", "limit", "leg_trace"})


def _flag_form(node: ast.expr, subject: str | None) -> object:
    """A handed value as a COMPARABLE form: a literal, or a normalised expression.

    ⛔ ``subject`` IS REQUIRED AND IS BOUND. Abstracting any left-hand `Name`
    (`subject = node.left.id`) compared `include_terminal=k in ("object", "subject")`
    EQUAL to the reference's `entity_type in ("object", "subject")` — measured green
    with `k` as the width, i.e. the probe reading a terminal-EXCLUDED population on
    the object/subject legs while `_fts_rows` reads terminal-included, and nothing
    else in the suite can see it (`check_leg_ids` cannot fire: fewer rows, all inside
    the population; the re-cut artifact just publishes an inflated miss). So the
    caller hands the site's OWN leg selector, and a condition derived from anything
    else is returned with its raw shape — which never equals the reference's.

    ⛔ THE LITERAL-ONLY COMPARISON WAS MEASURED GREEN OVER A REAL BREAK. Comparing
    only when both nodes were `ast.Constant` skipped exactly the derived flags —
    `include_terminal=kind in ("object", "subject")` is an `ast.Compare` — so
    editing the probe alone to `include_terminal=False` (or `=True`, or to a
    one-element tuple) left this pin green, and the census cannot see it either (the
    grant exempts the owner by NAME). That is the shape this pin exists to close:
    the probe would read a different population than the leg it calls a mirror.
    Ported from `tests/test_window_quality_m2.py::_expr_form`, which closed the same
    hole for the other `_fts_rows` mirror. The operator and the comparator constants
    are kept as well, so a narrowing (`kind in ("object",)`) reds.
    """
    try:
        return ("literal", ast.literal_eval(node))
    except (ValueError, SyntaxError):
        pass
    if not (subject and isinstance(node, ast.Compare)
            and isinstance(node.left, ast.Name) and node.left.id == subject):
        return ("raw", ast.dump(node))

    class _Abstract(ast.NodeTransformer):
        def visit_Name(self, inner: ast.Name) -> ast.Name:
            if inner.id != subject:
                return inner
            return ast.copy_location(ast.Name(id="<arg>", ctx=inner.ctx), inner)

    return ("expr", ast.dump(_Abstract().visit(ast.parse(ast.unparse(node), mode="eval").body)))


def _read_call_keywords(path: Path, owner: str) -> tuple[dict[str, ast.expr], tuple[str, ...]]:
    """The keyword arguments ONE function hands ``(sdk.)tortoise_fts_query``.

    Returns ``(named, splats)`` — the keyword VALUE nodes by name, and the
    ``**mapping`` names in source order. A splat hands the read names no reader of
    this file can see, so it is returned rather than dropped: the caller has to say
    what it knows about it. A missing owner, a re-shaped call or a second call in
    the same owner RAISES, because "found no site" must never read as "the two sites
    agree".
    """
    tree = ast.parse(path.read_text())
    owners = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == owner]
    assert len(owners) == 1, f"{path.name}:{owner} matched {len(owners)} definitions"
    calls = [n for n in ast.walk(owners[0]) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
             in ("tortoise_fts_query", "_tortoise_fts_query")]
    assert len(calls) == 1, (
        f"{path.name}:{owner} calls the rerankable read {len(calls)} times — this pin "
        "reads ONE call's keywords, and a second call would be unguarded")
    named = {k.arg: k.value for k in calls[0].keywords if k.arg is not None}
    splats = tuple(getattr(k.value, "id", "<expr>") for k in calls[0].keywords if k.arg is None)
    return named, splats


def _read_call_node(path: Path, owner: str) -> ast.Call:
    """The ONE ``(sdk.)tortoise_fts_query`` call inside ``owner``."""
    tree = ast.parse(path.read_text())
    owners = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == owner]
    assert len(owners) == 1, f"{path.name}:{owner} matched {len(owners)} definitions"
    calls = [n for n in ast.walk(owners[0]) if isinstance(n, ast.Call)
             and (getattr(n.func, "attr", None) or getattr(n.func, "id", None))
             in ("tortoise_fts_query", "_tortoise_fts_query")]
    assert len(calls) == 1, f"{path.name}:{owner} holds {len(calls)} read calls"
    return calls[0]


def _sdk_construction(path: Path) -> str:
    """What the module-level ``sdk`` NAME is assigned (unparsed).

    ``cur["sdk"] = sdk`` pins the SLOT to a name; the name itself is this, and a
    wrapper only has to rebind the name to replace every read behind the slot.
    """
    tree = ast.parse(path.read_text())
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    stores = [n for n in ast.walk(tree)
              if isinstance(n, ast.Name) and n.id == "sdk" and isinstance(n.ctx, ast.Store)]
    assert len(stores) == 1, (
        f"{path.name} assigns `sdk` at {len(stores)} places — this pin knows one")
    element, container = stores[0], stores[0]
    while isinstance(parents.get(id(container)), ast.Tuple):
        element, container = container, parents[id(container)]
    assign = parents.get(id(container))
    if isinstance(assign, ast.Assign) and isinstance(assign.value, ast.Tuple) \
            and isinstance(container, ast.Tuple):
        for left, right in zip(container.elts, assign.value.elts, strict=True):
            if left is element:
                return ast.unparse(right)
    assert isinstance(assign, ast.Assign), f"{path.name}: `sdk` is not plainly assigned"
    return ast.unparse(assign.value)


def _receiver_relays(path: Path) -> list[str]:
    """Every way the file could swap the read's receiver or its method.

    ⛔ REFUSED OUTRIGHT, NOT ENUMERATED. The tool has no attribute store, no
    ``setattr``/``getattr``/``__setattr__`` call and no ``__dict__``/``vars()`` mention at
    all, so a name-shaped check was always going to be one spelling behind: a
    constant-string match missed ``setattr(sdk, "_tortoise" + "_fts_query", …)``, and a
    bare-``setattr`` match missed ``builtins.setattr(...)`` and
    ``sdk.__dict__["_tortoise_fts_query"] = …`` (all three measured green). Any route to
    the read that is not ``cur["sdk"]._tortoise_fts_query(...)`` is out of scope here.
    """
    tree = ast.parse(path.read_text())
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            found.append(f"{path.name}:{node.lineno} stores an attribute ({node.attr})")
        if isinstance(node, ast.Attribute) and node.attr == "__dict__":
            found.append(f"{path.name}:{node.lineno} reaches an object's __dict__")
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store) \
                and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str) \
                and node.slice.value in ("tortoise_fts_query", "_tortoise_fts_query"):
            found.append(
                f"{path.name}:{node.lineno} stores the read's method by subscript")
        if (isinstance(node, ast.Call)
                and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
                in ("setattr", "getattr", "__setattr__", "vars")):
            found.append(
                f"{path.name}:{node.lineno} calls "
                f"{getattr(node.func, 'id', None) or getattr(node.func, 'attr', None)}")
    return sorted(found)


def _sdk_bindings(path: Path) -> list[str]:
    """What every ``cur["sdk"] = ...`` store in the file is assigned FROM.

    A receiver can be swapped for a wrapper in ONE line, so the binding is read
    structurally: each ``Store`` subscript keyed ``"sdk"`` must sit in a tuple
    assignment whose matching right-hand side is a bare name, and that name is what
    is returned here (``["sdk"]`` on the shipped tool).
    """
    tree = ast.parse(path.read_text())
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                and isinstance(node.slice, ast.Constant) and node.slice.value == "sdk"):
            continue
        element, container = node, node
        while isinstance(parents.get(id(container)), ast.Tuple):
            element = container
            container = parents[id(container)]
        assign = parents.get(id(container))
        assert isinstance(assign, ast.Assign), (
            f"{path.name}:{node.lineno} stores `cur[\"sdk\"]` outside a plain assignment")
        if isinstance(container, ast.Tuple):
            index = next(i for i, e in enumerate(container.elts) if e is element)
            value = (assign.value.elts[index]
                     if isinstance(assign.value, ast.Tuple) and index < len(assign.value.elts)
                     else assign.value)
        else:
            value = assign.value
        out.append(ast.unparse(value))
    return out


def _owner_node(path: Path, owner: str) -> ast.AST:
    """The ONE definition named ``owner`` in ``path``."""
    tree = ast.parse(path.read_text())
    owners = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == owner]
    assert len(owners) == 1, f"{path.name}:{owner} matched {len(owners)} definitions"
    return owners[0]


def _function_params(path: Path, owner: str) -> list[str]:
    """``owner``'s positional parameter names, in order."""
    tree = ast.parse(path.read_text())
    owners = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == owner]
    assert len(owners) == 1, f"{path.name}:{owner} matched {len(owners)} definitions"
    return [a.arg for a in owners[0].args.args]


def _read_call_widths(path: Path) -> list[tuple[int, ast.expr]]:
    """Every call to the probe's OWN ``query()``: its line and the width it hands.

    ``query`` is a local function, so the third positional argument is the ``limit``
    the read runs at — the number the rerank's composition rule compares against the
    pool, and therefore the number the census grant's safety rests on.
    """
    tree = ast.parse(path.read_text())
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "query"]
    widths = []
    for call in calls:
        assert len(call.args) == 3, (
            f"{path.name}:{call.lineno} calls `query()` with {len(call.args)} positional "
            "arguments — this pin reads the third as the width")
        widths.append((call.lineno, call.args[2]))
    return sorted(widths)


def test_the_probe_reads_the_exact_leg_fts_rows_runs():
    """The probe's ``query()`` is a MIRROR, and the census grant says so.

    ``tests/test_rerank_pool.py``'s ``intentional_keep`` row for this file exists on
    the ground that ``query()``'s docstring claims *"EXACTLY the leg ``_fts_rows``
    runs, at width ``k``"* — it passes the same two lever flags the capture leg
    passes and omits ``_rerank_candidates`` because ``_fts_rows`` omits it (the
    capture path keeps the rerank deliberately: the ``OVERRIDES:`` record at
    ``tortoise/extractor_v2.py:2931``, cited by ``tortoise/sdk.py`` beside its
    ``_rerank_candidates`` parameter, and #12297's measured recall@3). That row can
    only say the file is ALLOWED to run the lever, so the contract it cites is
    ASSERTED here against the leg it names: without this, adding
    ``_rerank_candidates=False`` to the probe would leave the census green and
    silently make every M5 figure describe a reader the capture path never runs (the
    shape ``test_window_quality_m2.py::
    test_the_mirror_reads_the_exact_lever_flags_its_reference_leg_does`` closed for
    the other ``_fts_rows`` mirror).

    AND the width: composition can only move when ``limit < _pool_n`` (``sdk.py``'s
    ``_rerank_candidates`` parameter COMMENT (line 45702), and the pool slice it
    describes), so the grant
    is inert at the width the probe MEASURES
    only while ``DEEP_LIMIT`` stays at or above the pool the read would resolve ONCE THE
    GATE IS ARMED — a raised default, and the env knob the width really resolves
    through, are both pinned. The only sub-pool read this file issues is the width-1
    warm-up, whose result is discarded.
    """
    probe, probe_splats = _read_call_keywords(TOOL, "query")
    reference, reference_splats = _read_call_keywords(
        ROOT / "tortoise" / "extractor_v2.py", "_fts_rows")
    # Anti-vacuity: a derivation that silently found nothing would make every
    # comparison below trivially true.
    levers = {"_record_reads", "_valid_time_rerank", "include_terminal"}
    assert levers <= set(reference), (
        f"the reference leg passes {sorted(reference)} — the levers this pin is about "
        "are not among them, so the mirror claim has moved and this pin is stale")
    assert not probe_splats, (
        f"tools/miss_rate_m5.py::query splats {list(probe_splats)} into the read, so "
        "whatever names that mapping carries are invisible to this pin")
    assert reference_splats == ("extra",), (
        f"extractor_v2.py::_fts_rows splats {list(reference_splats)}; the only splat "
        "this pin knows is `**extra` (#4509's per-session `exclude_turn_echo_session`)")
    # ⛔ AND WHAT THE SPLAT CARRIES — which an AST scan cannot answer. `**extra` is
    # checked by NAME above, and a lever hiding inside it left every AST shape
    # GREEN: a dict key, a nested `**{...}`, a subscript assignment, and — the one
    # that killed the AST guard — a SPLIT literal (`"_rerank" "_candidates"`, or
    # `extra.update({"_rerank" + "_candidates": False})`), measured. The capture
    # path's own behaviour is therefore driven directly in
    # `test_the_capture_leg_hands_the_read_no_rerank_opt_out` below, which needs no
    # spelling. What stays here is the AST-visible half: the reference must not gain
    # the lever as a KEYWORD either.
    additive = set(probe) - set(reference)
    assert additive <= {"leg_trace"}, (
        f"the probe passes keyword(s) the capture leg does not: {sorted(additive)} — "
        "only the trace sink may be added")
    # ⛔ AND THE SINK ITSELF, separately: `leg_trace` is the probe's OWN accounting
    # (`legs_seen` is built from it), so LOSING it is the quiet direction the
    # subset test above cannot name — a dropped coordinate used to report an empty
    # list and advise adding the keyword that had just been removed.
    assert "leg_trace" in probe, (
        "tools/miss_rate_m5.py::query no longer passes `leg_trace` — the probe's own "
        "`legs_seen` accounting is built from that trace, so the run would publish "
        "`legs_seen` claims it never observed")
    assert not set(reference) - set(probe), (
        f"the probe does NOT pass {sorted(set(reference) - set(probe))}, which the "
        "capture leg does — the mirror has been broken in the quiet direction")
    # ⛔ AND THE RECEIVER. The keyword comparison above constrains the CALL SITE, not
    # what the read is invoked ON: a wrapper interposed between the SDK and the call
    # (`cur["sdk"] = _TracedSdk(sdk)` with a `_tortoise_fts_query` that appends
    # `_rerank_candidates=False`) reproduced the exact break this whole change exists to
    # prevent — the probe reading a rerank-free reader — with BOTH new pins and the
    # census GREEN (the wrapper's inner call is a call site that opts out, and the
    # census's rule is "opt out OR be granted"). So the receiver is pinned by name, the
    # tool is pinned to define no relay, the `sdk` NAME is pinned to the constructed
    # `TortoiseSDK(...)`, and the file may neither rebind the read's method nor reach it
    # through `setattr`/`getattr`.
    call_node = _read_call_node(TOOL, "query")
    receiver = call_node.func.value

    def _is_sdk_slot(node: ast.AST) -> bool:
        return (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "cur"
                and isinstance(node.slice, ast.Constant) and node.slice.value == "sdk")

    assert _is_sdk_slot(receiver), (
        "tools/miss_rate_m5.py::query no longer calls the read on the `cur[\"sdk\"]` "
        f"slot ({ast.unparse(receiver)}) — an interposed wrapper can append a lever the "
        "keyword list above never sees")
    relays = [n.name for n in ast.walk(ast.parse(TOOL.read_text()))
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name in ("tortoise_fts_query", "_tortoise_fts_query")]
    assert not relays, (
        f"tools/miss_rate_m5.py defines {relays} — a relay that forwards to the read is "
        "exactly where a lever can be appended invisibly")
    # ⛔ AND WHAT THE RECEIVER'S NAME IS BOUND TO, because `cur["sdk"] = sdk` only says
    # the slot holds whatever `sdk` names: rebinding that name to a wrapper
    # (`sdk = _TracedSdk(sdk)`) or monkeypatching the method onto it
    # (`setattr(sdk, "_tortoise_fts_query", …)` / `sdk._tortoise_fts_query = …`) put a
    # rerank-free reader behind the probe's reads with EVERY pin above still green —
    # measured, and the census cannot see it either (its walk finds the inner call
    # already opted out, and its rule is "opt out OR be granted").
    assert _sdk_construction(TOOL).startswith("TortoiseSDK("), (
        f"the tool's `sdk` name is bound from {_sdk_construction(TOOL)!r}, not the "
        "constructed `TortoiseSDK(...)` — the probe's reads must run on the SDK the tool "
        "built, not on a wrapper around it")
    assert not _receiver_relays(TOOL), (
        "tools/miss_rate_m5.py stores an attribute or reaches one dynamically: "
        f"{_receiver_relays(TOOL)} — that is where a lever can be appended invisibly")
    assert _sdk_bindings(TOOL) == ['sdk'], (
        f"`cur[\"sdk\"]` is bound from {_sdk_bindings(TOOL)} — the probe's reads must run "
        "on the SDK object the tool was given, not on a wrapper around it")
    # The lever the census grant turns ON must be absent at BOTH sites.
    for site, kw in (("probe", probe), ("reference", reference)):
        assert "_rerank_candidates" not in kw, (
            f"the {site} now passes `_rerank_candidates` — the capture path KEEPS the "
            "rerank (its OVERRIDES record), so opting out of it is a different reader")
    # ⛔ THE COORDINATES ARE NOT A FREE PASS EITHER. They may differ in NAME (each
    # site names its own parameter), but each must still be handed as that site's
    # OWN PARAMETER — a hardcoded leg (`entity_type="point"`) or width (`limit=3`)
    # at either site would leave the loop below silent, because both are excluded
    # from it as coordinates. Measured: both of those stayed green.
    def _selector(kw: dict[str, ast.expr], site: str) -> str:
        node = kw.get("entity_type")
        assert isinstance(node, ast.Name), (
            f"the {site} hands `entity_type={ast.unparse(node) if node else '<missing>'}`, "
            "not its own parameter — a hardcoded leg reads a different population, and "
            "the comparison below cannot see a coordinate")
        return node.id

    probe_subject = _selector(probe, "probe")
    reference_subject = _selector(reference, "reference")
    assert probe_subject in _function_params(TOOL, "query"), (
        f"the probe hands `entity_type` from {probe_subject!r}, which is not one of "
        f"`query()`'s parameters — a local or a hardcoded leg is not the caller's")
    assert reference_subject in _function_params(
        ROOT / "tortoise" / "extractor_v2.py", "_fts_rows"), (
        f"the reference hands `entity_type` from {reference_subject!r}, which is not one "
        "of `_fts_rows`'s parameters")
    assert probe_subject != reference_subject, (
        f"both sites hand `entity_type` from a parameter named {probe_subject!r} — one of "
        "them is no longer forwarding its own argument, so the derived-flag comparison "
        "below would abstract the same name on both sides and prove nothing")
    # ⛔ THE REFERENCE'S WIDTH, the other coordinate: hardcoding it there
    # (`limit=3`) leaves the probe's 50-row read comparing against a 3-row leg —
    # measured green — so it must be `_fts_rows`'s own `limit` parameter, and the
    # probe's the same.
    reference_params = _function_params(ROOT / "tortoise" / "extractor_v2.py", "_fts_rows")
    ref_width = reference.get("limit")
    assert isinstance(ref_width, ast.Name) and ref_width.id in reference_params, (
        "the reference hands `limit="
        f"{ast.unparse(ref_width) if ref_width else '<missing>'}`, not its own parameter "
        f"({reference_params}) — the mirror is then comparing against a different width")
    # ⛔ THE WIDTH IS THE OWNER'S OWN THIRD PARAMETER, not merely "a Name": an inner
    # alias (`limit=_W` with `_W = 3`) satisfied a bare `isinstance(..., Name)` while
    # the read ran at width 3 and the artifact still printed `deep_limit: 50` —
    # measured green. Pinning the parameter NAME closes it, because `_read_call_widths`
    # below already pins what the callers hand that parameter.
    width = probe.get("limit")
    query_params = _function_params(TOOL, "query")
    assert len(query_params) >= 3, (
        f"`query()` no longer takes three positional parameters ({query_params}) — this "
        "pin reads the third as the width")
    assert isinstance(width, ast.Name) and width.id == query_params[2], (
        "the probe hands `limit="
        f"{ast.unparse(width) if width else '<missing>'}`, not `{query_params[2]}` — its "
        "own third parameter. An alias between the parameter and the read is exactly how "
        "the measured width escapes the arithmetic below")
    # ⛔ AND NO PARAMETER OF `query()` MAY BE REBOUND IN ITS BODY. The width walk below
    # covered index 2 only, so `kind = "point"` as the body's first statement red the
    # leg to points with every arm above GREEN (measured) — the same hole the width
    # case closed, one parameter over. The body binds none of them today.
    rebound = sorted(
        (name, n.lineno)
        for name in query_params
        for n in ast.walk(_owner_node(TOOL, "query"))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == name)
    assert not rebound, (
        f"`query()` rebinds {sorted({name for name, _ in rebound})} at line(s) "
        f"{[lineno for _, lineno in rebound]} — the arguments handed to the read are no "
        "longer the ones the callers passed, so nothing here can see what it really reads")
    # …and every shared name that is NOT a site coordinate must agree in FORM: a
    # literal is compared as its value, a derived flag as its normalised expression
    # over the site's OWN leg selector.
    shared = (set(probe) & set(reference)) - _SITE_COORDINATES
    assert shared == levers, (
        f"the shared non-coordinate keywords are {sorted(shared)}, not the levers this "
        "pin claims to compare — a new keyword needs a decision here, not a pass")
    for name in sorted(shared):
        left = _flag_form(probe[name], probe_subject)
        right = _flag_form(reference[name], reference_subject)
        assert left == right, (
            f"`{name}` is {left!r} at the probe and {right!r} at `_fts_rows` — a "
            "mirror that disagrees with its reference leg describes a reader the "
            "capture path does not run")
    # ⛔ AND `DEEP_LIMIT` ITSELF CANNOT BE SHADOWED. The name is pinned at the call
    # sites, but `DEEP_LIMIT = 3` as the first statement of `measure()`'s loop body
    # reinstated the literal-3 break with every assertion above GREEN (measured); the
    # arithmetic below reads the IMPORTED constant and cannot see a local. The module's
    # OWN definition is the one legitimate store.
    tool_tree = ast.parse(TOOL.read_text())
    tool_parents: dict[int, ast.AST] = {}
    for _node in ast.walk(tool_tree):
        for _child in ast.iter_child_nodes(_node):
            tool_parents[id(_child)] = _node
    deep_stores = [n for n in ast.walk(tool_tree)
                   if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
                   and n.id == "DEEP_LIMIT"]
    module_defs = [n for n in deep_stores
                   if isinstance(tool_parents.get(id(tool_parents.get(id(n)))), ast.Module)]
    assert len(deep_stores) == 1 and len(module_defs) == 1 and module_defs[0] is deep_stores[0], (
        "tools/miss_rate_m5.py rebinds `DEEP_LIMIT` away from its module-level "
        f"definition (stores at {[n.lineno for n in deep_stores]}) — the width the "
        "measured read runs at is then not the constant asserted below")
    # ⛔ AND THE WIDTH THE MEASURED CALL ACTUALLY HANDS. The arithmetic below pins a
    # module constant; nothing tied the read to it, so `query(kind, row["query"], 3)`
    # — a width BELOW the pool, i.e. exactly the re-pickable case — stayed green
    # while the artifact still reported `deep_limit: 50` (measured). So every call to
    # the probe's own `query()` is pinned to a width the grant is safe at: the
    # sampled read at `DEEP_LIMIT`, the discarded warm-up at the literal 1.
    widths = _read_call_widths(TOOL)
    measured = [(ln, w) for ln, w in widths
                if isinstance(w, ast.Name) and w.id == "DEEP_LIMIT"]
    warm = [(ln, w) for ln, w in widths
            if isinstance(w, ast.Constant) and w.value == 1]
    assert len(widths) == 2 and len(measured) == 1 and len(warm) == 1, (
        "the probe's `query()` calls are no longer exactly one sampled read at "
        f"`DEEP_LIMIT` plus one width-1 warm-up: {[(ln, ast.unparse(w)) for ln, w in widths]} "
        "— this pin knows those two widths, and any other one is unguarded")
    # …and the arithmetic that keeps the granted lever out of the MEASURED read.
    # ⛔ `>=`, NOT `>`: `rerank_pool` is a PERMUTATION, never a filter
    # (`tortoise/rerank.py` — "same length, same identities, same objects
    # reordered"), so at `pool == limit` the whole page is permuted and the ID SET
    # the probe counts is unchanged. The strict form false-RED a safe configuration
    # (`TORTOISE_FUSED_RERANK_POOL = DEEP_LIMIT`) and was measured doing so.
    assert DEEP_LIMIT >= DEFAULT_FUSED_RERANK_POOL, (
        f"DEEP_LIMIT={DEEP_LIMIT} is below the rerank pool default "
        f"({DEFAULT_FUSED_RERANK_POOL}): composition can move when `limit < pool`, so "
        "the granted lever would start re-picking which rows the probe counts")
    # ⛔ THE GATE-ARMED CASE, which the default alone does not cover: the read
    # resolves its pool through `fused_rerank_pool_width()`, so an ambient
    # `TORTOISE_FUSED_RERANK_POOL` above `DEEP_LIMIT` would put the measured page
    # back inside the re-pickable window while every assertion above stayed green.
    # Checked only when the gate is armed: with the lever OFF nothing can re-pick.
    if rerank_pool_enabled():
        armed_width = fused_rerank_pool_width()
        assert armed_width <= DEEP_LIMIT, (
            f"the fused-rerank gate is armed and resolves its pool width to {armed_width}, "
            f"which is ABOVE DEEP_LIMIT={DEEP_LIMIT} — the probe's measured page would be "
            "re-picked by the very lever the census grant declares inert; run the "
            "measurement with the gate off or a pool width at or below the depth")


def test_the_capture_leg_hands_the_read_no_rerank_opt_out():
    """`extractor_v2._fts_rows` keeps the rerank — measured by DRIVING it.

    ⛔ WHY THIS IS NOT ANOTHER AST SCAN. The census grant says the probe mirrors the
    capture leg, and the capture leg is recorded as KEEPING the lever (its
    ``OVERRIDES:`` record at ``extractor_v2.py:2931``). An AST scan cannot hold that
    line: the leg builds a ``**extra`` mapping, so the lever can reach the read as a
    dict key, a nested ``**{...}``, a subscript assignment, or a SPLIT literal
    (``"_rerank" "_candidates"`` / ``extra.update({"_rerank" + "_candidates": False})``)
    — every one of those was measured GREEN against a string-constant walk, and the
    call-site census cannot see them either (it reads ``ast.keyword``s only). So the
    leg is CALLED, with a stub sdk that records what the read actually receives: no
    spelling, no indirection, no mapping shape can survive it.

    The same call also pins the two levers BEHAVIOURALLY — the AST comparison in
    ``test_the_probe_reads_the_exact_leg_fts_rows_runs`` reads the same literals, but
    only here is it observable that the derived ``include_terminal`` really comes out
    TRUE for the object/subject legs and FALSE for points.

    Population, derived rather than remembered: ``_derive_queries`` emits exactly
    ``object``/``event``/``point`` (``extractor_v2.py:2822``) and its only caller
    passes every one of those to ``_fts_rows`` (``:3420``), so all three are driven
    here; ``subject`` is driven for symmetry with the other stub drive
    (`tests/test_time_aware_sdk_2520.py`'s four-leg one), NOT because production reaches
    it — `_derive_queries` never emits it. ⛔ ``event`` was MISSING from the first
    shape list and a leg-conditional opt-out
    (``if entity_type == "event": extra = dict(extra, _rerank_candidates=False)``)
    was measured fully GREEN — the population is the guard, not the four shapes
    ``extra`` happens to build.

    Residual, named: a branch reachable only under another FLAG (an env gate), or keyed
    on a width this pin does not drive, is not covered; anything keyed on the entity type
    or on the two driven widths (3 and the probe's own ``DEEP_LIMIT``) is.
    """
    from tortoise.extractor_v2 import _fts_rows  # heavy module: imported at use, not at collection

    class _Recorder:
        """A stub sdk: it records the read's keyword arguments and returns no rows."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        def _tortoise_fts_query(self, *args, **kwargs):
            self.calls.append(kwargs)
            return []

    shapes = (("point", None), ("point", "session-1"), ("object", None),
              ("event", None), ("subject", "session-1"))
    # ⛔ AND THE WIDTH THE PROBE'S OWN MEASURED READ USES. Driving only ``limit=3`` left
    # a width-keyed opt-out (`if limit >= 40: extra = dict(extra, _rerank_candidates=False)`)
    # GREEN (measured) — a branch that is neither env-gated nor entity-keyed, so it is
    # the same class as the ``event`` gap above, on the other axis.
    for limit in (3, DEEP_LIMIT):
        for entity_type, session in shapes:
            recorder = _Recorder()
            _fts_rows(recorder, entity_type, "mirror probe", limit=limit, session_id=session)
            assert len(recorder.calls) == 1, (
                f"_fts_rows({entity_type!r}, limit={limit}, session_id={session!r}) made "
                f"{len(recorder.calls)} reads — this pin reads the ONE it makes")
            kwargs = recorder.calls[0]
            assert "_rerank_candidates" not in kwargs, (
                f"`extractor_v2._fts_rows` hands `_rerank_candidates="
                f"{kwargs['_rerank_candidates']!r}` on the {entity_type} leg at "
                f"limit={limit}: the capture path is recorded as KEEPING the rerank, so "
                "this is a departure that needs its own OVERRIDES record and this pin "
                "updated")
            assert kwargs["_record_reads"] is False, kwargs
            assert kwargs["_valid_time_rerank"] is False, kwargs
            assert kwargs["include_terminal"] is (entity_type in ("object", "subject")), (
                f"the {entity_type} leg now reads include_terminal="
                f"{kwargs['include_terminal']!r} — the capture path opts the entity/subject "
                "prior INTO terminal rows (#3301) and leaves terminal Points out")
            assert kwargs["entity_type"] == entity_type, kwargs
            assert kwargs["limit"] == limit, (
                f"the read ran at limit={kwargs['limit']!r} when the leg was called with "
                f"limit={limit} — the width is the one number the composition rule is "
                "stated over")
            if entity_type == "point" and session:
                assert kwargs["exclude_turn_echo_session"] == session, kwargs
