"""Pins M2's arithmetic, its two invariants, and the artifact its record cites.

The evidence record (`docs/evidence/12186-m2-windowed-quality/README.md`) and
`SEARCH-ARCHITECTURE.md` §9.1 publish numbers a reader will quote, and they sit
on an instrument whose failure mode is SILENT: an arm measured against a graph
state that never existed still produces a table of plausible rates. So three
things are pinned here, and each closes a different way the record can rot:

1. **The arithmetic.** ``resident_ids`` / ``eviction_order`` / ``classify_rows``
   / ``summarise`` are pure, so their contract is asserted directly — including
   the seed-independence that made the instrument's first control arm report a
   DISJOINT subset (0 % resident at coverage 0.50) instead of an independent one.
2. **The invariants the instrument refuses on.** A demoted node cannot be
   returned, so ``found@deep`` and every ``recall@k`` are bounded by the resident
   share, and the graph must hold exactly the intended set. Those checks live in
   ``run()``, which needs a live FalkorDB; what is pinned here is that the
   COMMITTED ARTIFACT satisfies them — so an artifact regenerated on a broken
   graph reds instead of being quoted.
3. **The record against the artifact.** §9.1 and the README quote figures; each
   is recomputed from ``measurement.json`` here, so the doc cannot state a number
   the evidence does not hold.

⛔ None of this runs the measurement — it needs a live FalkorDB and a real capture
corpus. A test that needed a graph would be skipped on CI and would guard
nothing. What is pinned is the reasoning surface and the committed artifact.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs" / "evidence" / "12186-m2-windowed-quality"
ARTIFACT = EVIDENCE / "measurement.json"
README = EVIDENCE / "README.md"
ARCH = ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md"
TOOL = ROOT / "tools" / "window_quality_m2.py"
CHANGELOG = ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE-CHANGELOG.md"

sys.path.insert(0, str(ROOT))

from tools.window_quality_m2 import (  # noqa: E402
    DEEP_LIMIT,
    EVICT_SEED_OFFSET,
    ID_PREFIX,
    KS,
    build_point_population,
    classify_rows,
    eviction_order,
    harvest_turn_texts,
    query_span,
    resident_ids,
    summarise,
)


@pytest.fixture(scope="module")
def artifact() -> dict:
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


# ── 1. the pure arithmetic ───────────────────────────────────────────────────

def test_resident_ids_keeps_the_hot_suffix_of_an_eviction_order():
    # `ordered_ids` is COLDEST FIRST, so the retained share is the SUFFIX.
    assert resident_ids(["a", "b", "c", "d"], 0.5) == ["c", "d"]
    assert resident_ids(["a", "b", "c", "d"], 1.0) == ["a", "b", "c", "d"]


def test_resident_ids_never_rounds_a_positive_coverage_down_to_an_empty_graph():
    # A coverage that rounds to 0 measures "the graph is empty", which is a
    # different experiment from "almost everything was demoted".
    assert resident_ids(["a", "b"], 0.01) == ["b"]


def test_resident_ids_rejects_an_out_of_range_coverage():
    with pytest.raises(ValueError):
        resident_ids(["a"], 1.5)
    with pytest.raises(ValueError):
        resident_ids(["a"], -0.1)


def test_eviction_order_tail_is_total_and_coldest_first():
    rows = [{"id": "hot", "popularity": 9},
            {"id": "cold", "popularity": 1},
            {"id": "tie-b", "popularity": 5},
            {"id": "tie-a", "popularity": 5}]
    order = eviction_order(rows, "tail", 7)
    assert order[0] == "cold"
    assert order[-1] == "hot"
    # Ties break on id, so the order is TOTAL and reproducible across runs —
    # a Counter's insertion order is not a property to depend on.
    assert order == ["cold", "tie-a", "tie-b", "hot"]
    assert eviction_order(rows, "tail", 999) == order


def test_eviction_order_random_is_seeded_and_policy_invalid_is_loud():
    rows = [{"id": str(i), "popularity": i} for i in range(50)]
    assert eviction_order(rows, "random", 3) == eviction_order(rows, "random", 3)
    assert eviction_order(rows, "random", 3) != eviction_order(rows, "random", 4)
    with pytest.raises(ValueError):
        eviction_order(rows, "lru", 3)


def test_the_query_sample_and_the_random_eviction_order_are_independent():
    """⛔ THE REGRESSION THAT MADE THE CONTROL ARM USELESS.

    Both are permutations of the SAME population list, so drawing them from one
    seed makes them the SAME permutation and the retained suffix is disjoint from
    the sampled prefix. Measured on the instrument's first run: `resident_share`
    0.167 at coverage 0.75 and 0.000 at 0.50 — true about the graph, worthless as
    a control. The offset is what separates the streams.
    """
    assert EVICT_SEED_OFFSET != 0
    rows = [{"id": str(i), "popularity": i} for i in range(100)]
    sample = list(rows)
    import random
    random.Random(11901).shuffle(sample)
    sample_ids = {r["id"] for r in sample[:30]}

    collision = list(rows)
    random.Random(11901).shuffle(collision)
    collided_keep = set(resident_ids([r["id"] for r in collision], 0.5))

    real = list(rows)
    random.Random(11901 + EVICT_SEED_OFFSET).shuffle(real)
    real_keep = set(resident_ids([r["id"] for r in real], 0.5))

    # One shared seed: the halves are disjoint by construction.
    assert not (collided_keep & sample_ids)
    # Independent streams: the retained half overlaps the sample, as an
    # independent subset must.
    assert real_keep & sample_ids


def test_classify_rows_states_every_class_once():
    assert classify_rows(["x", "y"], "y")["hit"] == {k: True for k in KS}
    assert classify_rows(["x", "y"], "y")["top1_other"] is True
    assert classify_rows(["y"], "y")["top1_is_canonical"] is True
    empty = classify_rows([], "y")
    assert empty["empty"] is True
    assert empty["top1_other"] is False  # no top-1 is not a wrong top-1
    assert empty["found_deep"] is False


def test_classify_rows_found_deep_is_the_whole_read_not_a_slice():
    # The deep axis must NOT be `recall@len(ks)` — a canonical at rank 20 is
    # findable and is exactly what separates "the leg cannot rank it" from "the
    # window does not hold it".
    rows = ["other"] * 20 + ["target"]
    assert classify_rows(rows, "target")["found_deep"] is True
    assert classify_rows(rows, "target")["hit"] == {k: False for k in KS}
    assert max(KS) < DEEP_LIMIT


def test_summarise_scopes_the_demoted_rates_to_the_demoted_subset():
    per_query = [
        {"resident": True, "empty": False, "top1_other": False,
         "found_deep": True, "hit": {k: True for k in KS}},
        {"resident": False, "empty": True, "top1_other": False,
         "found_deep": False, "hit": {k: False for k in KS}},
        {"resident": False, "empty": False, "top1_other": True,
         "found_deep": False, "hit": {k: False for k in KS}},
    ]
    s = summarise(per_query)
    assert s["queries"] == 3
    assert s["demoted_queries"] == 2
    assert s["resident_share"] == pytest.approx(1 / 3)
    # The demoted-scoped rates use the DEMOTED denominator, not the query set.
    assert s["silent_zero"] == pytest.approx(1 / 2)
    assert s["wrong_not_miss"] == pytest.approx(1 / 2)
    assert s["misbind_at_1"] == pytest.approx(1 / 3)
    assert s["found_deep"] == pytest.approx(1 / 3)


def test_summarise_refuses_an_empty_query_set():
    with pytest.raises(ValueError):
        summarise([])


# ── 2. the Point query set is well-posed ─────────────────────────────────────

def test_query_span_rejects_structural_noise():
    # Real captures are full of `x x x x`, bullets and table pipes; a span built
    # from those is not a query, and measuring on it would look like a result.
    assert query_span("-- --- ### * * *") is None
    assert query_span("x " * 50) is None
    assert query_span("the reservation window holds closed components") == (
        "the reservation window holds closed")


def test_query_span_picks_the_most_selective_window_when_postings_are_given():
    text = "alpha beta gamma delta epsilon zeta eta theta"
    postings = {
        "alpha": {0}, "beta": {0}, "gamma": {0}, "delta": {0}, "epsilon": {0},
        # The second window's terms are held by exactly one document.
        "zeta": {0}, "eta": {0}, "theta": {0},
    }
    # Without postings the first window wins…
    assert query_span(text, n_tokens=5) == "alpha beta gamma delta epsilon"
    # …and with them the most selective window still wins, deterministically.
    picked = query_span(text, postings=postings, n_tokens=5)
    assert picked in {"alpha beta gamma delta epsilon", "beta gamma delta epsilon zeta",
                      "gamma delta epsilon zeta eta", "delta epsilon zeta eta theta"}
    assert picked == query_span(text, postings=postings, n_tokens=5)


def test_point_population_gives_every_query_exactly_one_canonical():
    """A query naming several claims has no canonical to recall, so it is dropped.

    The captures repeat boilerplate verbatim, so without the span dedupe several
    Points share one query and the arm would charge the window for an ambiguity
    the corpus created.
    """
    dup = ("the reservation window holds closed components across the merge boundary "
           "and more text here to pass the length gate")
    pop = build_point_population([dup, dup, dup], 10, sanitise=lambda s: s)
    assert len(pop) == 1
    assert len({r["query"] for r in pop}) == len(pop)
    assert len({r["id"] for r in pop}) == len(pop)


def test_point_population_drops_a_span_the_sanitiser_collapses():
    text = ("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
            "and more text here to pass the length gate for the point population")
    # A sanitiser that throws the span away must not leave a Point whose query is
    # empty — it is dropped instead.
    pop = build_point_population([text], 10, sanitise=lambda s: "")
    assert pop == []


def test_point_ids_carry_the_population_prefix():
    text = ("the reservation window holds closed components across the merge boundary "
            "and more text here to pass the length gate")
    pop = build_point_population([text], 10, sanitise=lambda s: s)
    assert pop and all(r["id"].startswith(ID_PREFIX["point"]) for r in pop)


def test_harvest_turn_texts_spreads_across_every_file(tmp_path):
    """⛔ The first real run read 6 of 22 files.

    Consuming the limit from the first files put the whole Point population on
    one probe-heavy day, whose turns share one boilerplate shape — a population
    that dense in near-duplicates makes every query ambiguous, and the artifact
    would have reported it as a retrieval result. The per-file budget is what
    keeps the population spanning the corpus.
    """
    for i in range(5):
        (tmp_path / f"f{i}.jsonl").write_text(
            "\n".join(
                json.dumps({"conversation": [
                    {"role": "user",
                     "content": f"file {i} turn {j} " + "word " * 60}]})
                for j in range(10)
            ) + "\n", encoding="utf-8")
    texts, stats = harvest_turn_texts(tmp_path, 10)
    assert stats["files"] == 5
    assert stats["files_contributed"] == 5
    assert len(texts) == 10
    # Both halves of the budget must be represented, not one file's ten turns.
    assert len({t.split()[1] for t in texts}) == 5


def test_harvest_turn_texts_counts_what_it_could_not_parse(tmp_path):
    (tmp_path / "bad.jsonl").write_text(
        "{not json}\n" + json.dumps({"conversation": []}) + "\n", encoding="utf-8")
    _, stats = harvest_turn_texts(tmp_path, 10)
    assert stats["parse_errors"] == 1
    assert stats["records_without_text"] == 1


# ── 3. the committed artifact satisfies the instrument's invariants ──────────

def test_the_artifact_records_a_keyword_only_lexical_run(artifact):
    """⛔ The claim is no wider than the measurement.

    Without the `embeddings` extra the fused read degrades to keyword-only, and
    `battery/runner/retrieval_preflight.py` names a keyword-only run published as
    a product measurement as the class it exists to prevent. For M2 this is the
    leg that matters (the one §6.2 keeps in the graph), but the artifact must SAY
    so, and this pins that it does.
    """
    assert artifact["retrieval"]["mode"] in {"keyword_only", "hybrid"}
    if not artifact["retrieval"]["has_embedder"]:
        assert artifact["retrieval"]["mode"] == "keyword_only"
    assert artifact["retrieval"]["legs_seen"].get("vector") in (False, None)


def test_the_artifact_records_that_the_loss_carries_no_signal(artifact):
    """§5 Q2 is violated by construction, and the record's claim rests on this."""
    assert artifact["signal"] == {
        "capture_prior_read_status": False,
        "capture_prior_truncation": False,
        "residency_marker_armed": False,
    }


def test_no_arm_returns_a_demoted_node(artifact):
    """INVARIANTS 1 **and** 3, re-derived from the artifact.

    A demoted node cannot be returned, so both axes are bounded by the resident
    share. The instrument refuses a run that violates this; this asserts the
    COMMITTED artifact satisfies it, so an artifact regenerated on a broken graph
    reds instead of being quoted.

    ⛔ The per-demoted rate is checked on EVERY arm of BOTH populations, and that
    is the load-bearing half (#12186 review P1-2). The aggregate `found_deep`
    bound is only as strong as the population's ceiling: on `:Object` the
    complete-graph rate is 1.0 so a leak must break it, but on `:Point` it is
    0.410 against a resident share of 0.5267, so up to ~35 leaked demoted queries
    (11.7 % of the set) pass the bound. Pinning the aggregate alone, or the
    per-demoted rate on the single `object/tail@0.5` arm, left the record's
    "no compensating leg" claim unverified on the population a wrong answer
    actually costs a duplicate on.
    """
    for kind, pop in artifact["populations"].items():
        for arm, data in pop["arms"].items():
            if arm == "complete":
                assert data["found_deep"] <= 1.0
                continue
            bound = data["resident_share"] + 1e-9
            assert data["found_deep"] <= bound, f"{kind}/{arm} found_deep"
            for k in KS:
                assert data[f"recall@{k}"] <= bound, f"{kind}/{arm} recall@{k}"
            # INVARIANT 3 — the strong form, unconditional on the ceiling.
            assert data["found_deep_of_demoted"] == pytest.approx(0.0), (
                f"{kind}/{arm}: a demoted node was returned at depth — the loss "
                f"is then NOT purely the eviction")


def test_every_arm_holds_really_holds_the_coverage_it_names(artifact):
    """INVARIANT 2 — the graph held exactly the set the arm claims.

    `coverage_delivered` is read from the graph, not from the intent, so this
    cannot pass over an over- or under-evicted arm.
    """
    for kind, pop in artifact["populations"].items():
        n = pop["nodes"]
        for arm, data in pop["arms"].items():
            if arm == "complete":
                continue
            assert data["coverage_delivered"] == pytest.approx(
                data["coverage_requested"], abs=1.0 / n + 1e-9), f"{kind}/{arm}"
            assert 0.0 < data["coverage_delivered"] <= 1.0


def test_the_artifact_holds_both_populations_at_the_declared_sizes(artifact):
    assert set(artifact["populations"]) == {"point", "object"}
    assert artifact["populations"]["object"]["nodes"] == 1000
    # ⛔ An exact count, not `> 0`: a population-construction regression that left
    # one node would still have produced 300 "queries" per arm and pass a `> 0`
    # pin, publishing per-arm rates over draws from a single node.
    assert artifact["populations"]["point"]["nodes"] == 786
    assert artifact["populations"]["object"]["queries"] == 300
    for pop in artifact["populations"].values():
        assert pop["queries"] == 300
        assert set(pop["arms"]) == {
            "complete"} | {f"{p}@{c:g}" for p in ("tail", "random")
                           for c in (0.9, 0.75, 0.5, 0.25, 0.1)}


# ── 4. the record against the artifact ───────────────────────────────────────

def _load_tool():
    """Import the instrument by PATH, so its constants are the shipped bytes."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("_wqm2_tool", TOOL)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_artifact_binds_its_own_provenance(artifact):
    """The three provenance blocks are PRESENT and actually self-consistent.

    ⛔ This is the pin `config/ci-surfaces.yml` claims and did not have (#12186
    review P1-1): the row said it pinned "the artifact's own `reproducibility` /
    `provenance` / `cleanup` blocks being present and self-consistent", and no
    test in this module read any of them. Measured then: setting
    `provenance.tool_sha256` to 64 zeros, `reproducibility.runs_compared` to 11
    and `found_deep_max_delta_pp` to 99.0, and `params.points` to 12 all left the
    suite green.

    The first assertion is the one that would have caught the real defect: the
    artifact's `reproducibility` block described 3 runs at 0.33 / 1.0 / 9.85
    while the bytes its `tool_sha256` named emitted 2 runs at 0.0 / 2.94 and a
    "17-43" gap — so the provenance claim bound the artifact to an instrument
    that did not produce it (#12186 review P0-1).
    """
    import hashlib

    tool = _load_tool()

    # (1) the block in the artifact IS the block the instrument emits.
    assert artifact["reproducibility"] == tool.REPRODUCIBILITY_BOUND, (
        "the artifact's `reproducibility` block is not the one the instrument "
        "emits — the provenance binding is then an assertion, not a measurement")

    # (2) the hash names THESE bytes.
    digest = hashlib.sha256(TOOL.read_bytes()).hexdigest()
    assert artifact["provenance"]["tool_sha256"] == digest, (
        "provenance.tool_sha256 does not name the shipped instrument — "
        "regenerate the artifact (or update the hash) after editing tools/")

    # (3) the bound is internally coherent: the deltas it claims to have
    #     measured are positive numbers, and the gap it leans on is real.
    rep = artifact["reproducibility"]
    assert rep["runs_compared"] >= 2, "a reproducibility bound needs >= 2 runs"
    for key in ("found_deep_max_delta_pp", "recall_at_k_max_delta_pp",
                "tie_counting_max_delta_pp"):
        assert isinstance(rep[key], (int, float)) and rep[key] >= 0.0, key
    # (5) the recorded gap range must be the COMPUTED one, so the constant cannot
    #     certify itself (#12186 review P2-2: `13 to 43` matched neither endpoint —
    #     the record's real gaps run 13.3 (misbind) to 48.3 (found@deep@0.5)).
    low, _, high = rep["conclusions_rest_on_gap_pp"].partition(" to ")
    want_low, want_high = _gap_endpoints(artifact)
    assert (low, high) == (want_low, want_high), (
        f"reproducibility.conclusions_rest_on_gap_pp says {low!r}-{high!r} but the "
        f"gaps the record quotes compute to {want_low}-{want_high}")
    low, _, high = rep["conclusions_rest_on_gap_pp"].partition(" to ")
    assert float(low) < float(high), "the recorded gap range is not a range"
    # the tie band must not be presented as small against that gap
    assert rep["tie_counting_max_delta_pp"] <= float(high)

    # (4) `cleanup` accounts for the graphs the run minted. Every arm is
    #     measured on its own graph and NONE is dropped by this tool — that is
    #     the documented design (`test_lane.py down` disposes of them), so a
    #     non-zero `graphs_dropped` means the note and the behaviour diverged.
    cleanup = artifact["cleanup"]
    assert cleanup["graphs_created"] > 0
    assert cleanup["graphs_dropped"] == 0, (
        "the tool drops no graph; if that is now false, the note must say so")


def _arms(artifact: dict, kind: str) -> dict:
    return artifact["populations"][kind]["arms"]


def test_the_object_window_is_exactly_lossy(artifact):
    """§9.1: "`found@deep` equals the resident share in every arm".

    This is the finding, not a coincidence of the run: what the window keeps it
    can still find, and what it demotes it never returns — nothing compensates.
    """
    for arm, data in _arms(artifact, "object").items():
        if arm == "complete":
            assert data["found_deep"] == 1.0
            continue
        assert data["found_deep"] == pytest.approx(data["resident_share"]), arm


def test_the_object_identity_is_a_object_law_not_a_point_one(artifact):
    """`found@deep == resident_share` is a `:Object` LAW; on `:Point` it COINCIDES.

    ⛔ The verdict block used to say the identity holds "in **no** `:Point` arm",
    and that absolute is false: `random@0.1` lands on it exactly (0.11 == 0.11),
    because at 10 % coverage the claim leg's own ceiling happens to bind at the
    resident share. Nothing pinned the `:Point` side —
    `test_the_object_window_is_exactly_lossy` reads `:Object` only — so an
    absolute the artifact refutes shipped green.

    The property worth pinning is the TOPOLOGY, not the coincidence: exactly one
    windowed `:Point` arm meets the share and every other one falls short. A
    re-run that turned the coincidence into a law (or a second coincidence into a
    pattern) would red here rather than quietly softening the finding.
    """
    windowed = {a: v for a, v in _arms(artifact, "point").items() if a != "complete"}
    meets = [a for a, v in windowed.items()
             if v["found_deep"] == pytest.approx(v["resident_share"], abs=5e-4)]
    assert meets == ["random@0.1"], (
        f"the :Point coincidence moved: {meets} — the record names `random@0.1`, "
        f"and a second one would make the identity a `:Point` law")
    short = [a for a, v in windowed.items()
             if v["found_deep"] < v["resident_share"] - 5e-4]
    assert len(short) == len(windowed) - 1, (
        "every other windowed :Point arm must fall SHORT of its resident share, "
        "or the claim leg has no ceiling and the finding is different")


def test_the_tie_band_is_below_the_gap_the_verdict_rests_on(artifact):
    """The band must be smaller than the floor, or the verdict cannot be read.

    ⛔ This is the invariant behind the record's "~3/4 of the gap" sentence, and
    it was unenforced: the prose could claim any ratio while the artifact's
    `conclusions_rest_on_gap_pp` floor stayed above the tie band only by luck.
    The claim is only meaningful while `tie_counting_max_delta_pp` < the floor;
    once the band reaches the floor, the smallest gap the verdict leans on is
    inside the noise and the record must stop quoting it as a gap.
    """
    rep = artifact["reproducibility"]
    low, sep, high = rep["conclusions_rest_on_gap_pp"].partition(" to ")
    assert sep, "conclusions_rest_on_gap_pp is not a 'A to B' range"
    assert float(low) < float(high), rep["conclusions_rest_on_gap_pp"]
    assert rep["tie_counting_max_delta_pp"] < float(low), (
        f"the tie band ({rep['tie_counting_max_delta_pp']} pp) is not below the "
        f"smallest verdict gap ({low} pp) — the ratio the record quotes is void")


def test_the_object_complete_recall_figures_match_the_record(artifact):
    """The BASELINE the whole curve is read against, pinned as properties.

    ⛔ Not literals. `recall@k` moves by up to 1.0 pp between runs of identical
    input (the artifact's own `reproducibility` block), so a decimal pin here
    re-stales on every re-run and teaches the next lane to bump the number —
    exactly the drift this module exists to stop. What the conclusion needs is
    that the complete graph is a HIGH-recall baseline and that the leg is exact
    at depth 50; both are asserted as bounds far outside the run-to-run band.
    """
    complete = _arms(artifact, "object")["complete"]
    assert complete["recall@3"] >= 0.85          # a high-recall baseline
    assert complete["recall@3"] <= complete["recall@5"] <= complete["recall@15"]
    assert complete["recall@15"] >= 0.95
    assert complete["found_deep"] == pytest.approx(1.0)   # everything resident
    # …and the baseline is NOT already broken, or the drop would be unmeasurable.
    assert complete["misbind_at_1"] <= 0.35


def test_the_half_coverage_row_matches_the_record(artifact):
    """§9.1's headline: at §8's own target size, recall@3 roughly HALVES.

    The magnitude is asserted as a bound that clears the run-to-run band by an
    order of magnitude (the band is <=1.0 pp; the collapse is ~42 points).
    """
    complete = _arms(artifact, "object")["complete"]
    half = _arms(artifact, "object")["tail@0.5"]
    drop = complete["recall@3"] - half["recall@3"]
    assert drop >= 0.35, drop                     # the collapse, not a wobble
    # the target size really is about half (§8 budgets ~77 MB against ~150 MB)
    assert 0.45 <= half["resident_share"] <= 0.55
    # …and no demoted node is retrievable at ANY rank, which is what makes the
    # loss equal to the eviction rather than merely correlated with it.
    assert half["found_deep_of_demoted"] == pytest.approx(0.0)
    assert half["found_deep"] == pytest.approx(half["resident_share"], abs=5e-4)


def test_the_policy_choice_is_not_a_lever(artifact):
    """No static order recovers more than a few percent of what the window costs.

    ⛔ This pin was green on the exact claim it was named for, and that claim was
    FALSE. It read only `recall@3` with a 0.06 tolerance — the largest `recall@3`
    divergence is 5.0 pp, inside it — and never read `found_deep`, `recall@10` or
    `recall@15`, which is where `tail` is AHEAD of `random`: +2.00 pp on
    `found_deep` at 0.50 (that metric's band is 0.33 pp) and +2.33 pp on `:Point`
    `recall@10` at 0.90. "Never buys back" is false; what is true, and what the
    verdict needs, is that the largest difference between the orders is a small
    fraction of the collapse both of them suffer.
    """
    metrics = ("found_deep", "recall@3", "recall@5", "recall@10", "recall@15")
    # ⛔ The ceiling IS the number the record publishes ("differ by at most 5.0
    # points on any gated metric"), not a looser tolerance around it. An earlier
    # cut bounded at 6.0 pp (and, through the collapse ratio, 7.25 pp) — both
    # ABOVE the published bound, so the prose could go false with the suite
    # green: setting `point/random@0.9 recall@10` to 0.252 makes the real
    # divergence 5.8 pp and all 36 tests still passed.
    published_bound = 0.05
    worst = 0.0
    for pop in ("object", "point"):
        arms = _arms(artifact, pop)
        for cov in ("0.9", "0.75", "0.5", "0.25", "0.1"):
            for m in metrics:
                d = abs(arms[f"tail@{cov}"][m] - arms[f"random@{cov}"][m])
                worst = max(worst, d)
                # 1e-9 absorbs float noise only: the measured maximum is exactly
                # 5.00 pp (object@0.25 recall@3), so the bound is tight, not slack.
                assert d <= published_bound + 1e-9, (pop, cov, m, d)
    # …and the thing that DOES dwarf it, which is what the verdict rests on.
    complete = _arms(artifact, "object")["complete"]
    half = _arms(artifact, "object")["tail@0.5"]
    collapse = complete["found_deep"] - half["found_deep"]
    assert collapse >= 0.42, collapse
    assert worst < 0.15 * collapse, (worst, collapse)


def test_a_demoted_claim_query_returns_another_claim_not_an_empty_answer(artifact):
    """§9.1: "every demoted query returns another claim, never an empty answer"."""
    for arm, data in _arms(artifact, "point").items():
        if arm == "complete":
            continue
        assert data["wrong_not_miss"] == pytest.approx(1.0), arm
        assert data["silent_zero"] == pytest.approx(0.0), arm
        # The deep axis must also show the loss, or the claim above would be
        # about the top-1 slot only.
        assert data["found_deep"] < data["resident_share"] + 5e-4, arm


def test_the_claim_population_ceiling_matches_the_record(artifact):
    """§9.1: the lexical leg's own ceiling, complete — 15.7 % recall@3, 71.3 % deep.

    ⛔ EXACT, not a bound, and the difference is not pedantry. The
    reproducibility band (recall@k <=1.0 pp, found@deep <=0.33 pp) governs whether
    a NEW run may be quoted against the old record; it does NOT relax a pin on a
    COMMITTED artifact, which never re-runs. Loosening these to `recall@3 <= 0.20`
    and `found_deep > recall@3` let the artifact's ceiling drift from 0.157 to
    0.05 and still pass — while §9.1 goes on publishing "15.7 %". The doc scan
    below is a MEMBERSHIP test over every figure the artifact holds, so it cannot
    tell a correct binding from a figure that merely exists somewhere; binding a
    QUOTED number to its QUANTITY is this test's whole job.
    """
    complete = _arms(artifact, "point")["complete"]
    assert complete["recall@3"] == pytest.approx(0.157, abs=5e-4)
    # …and the deep axis is not the same number, which is the point of the row.
    assert complete["found_deep"] == pytest.approx(0.713, abs=5e-4)
    # …and §9.1 quotes the same loss at the target size.
    assert _arms(artifact, "point")["tail@0.5"]["found_deep"] == pytest.approx(0.410, abs=5e-4)

#: Figures the docs legitimately state that are NOT artifact rates — declared
#: explicitly so the scan below can tell "a number the artifact holds" from "a
#: number the artifact never measured" without silently allowing anything.
_NON_ARTIFACT_FIGURES = {
    # §8's sizing (a budget in MB, quoted to bound the target size)
    "77", "150", "50",
    # the corpus mix (#11901's own audit, not this run). The two PERCENTAGES
    # (8.0 / 3.4) are derived in `_derived_figures` from `names_harvested` and are
    # deliberately NOT listed here: a literal shadows the derivation and the scan
    # then passes while the prose goes stale (#12186 review P2-3).
    "60", "30", "10", "22", "653", "786", "1000", "300", "11901",
    # §7's literature figure and §5's threshold
    "54",
    # the rule comparison the instrument's docstring records (a superseded rule)
    "19.0",
    # §9's own M-numbering and the other measurements' figures
    "90", "900",
    # the complete-graph share a windowed arm is a fraction OF (100 %)
    "100",
    # §8.1's budget overshoot that motivates the window (193 % of the 30 % budget)
    "193",
    # the ceiling the FIRST-window rule costs against the adopted one (19.0 - 15.7),
    # quoted as an approximation ("~3 points") and not an artifact rate
    "3",
    # ⛔ NOT hard-coded here, because the artifact DERIVES them and a literal would
    # survive a regenerated artifact while the prose went stale (#12186 review P2-4):
    #   the reproducibility band   <- artifact["reproducibility"] / REPRODUCIBILITY_BOUND
    #   the corpus mix 8.0 / 3.4   <- corpus.names_harvested
    #   the per-resident 87.1      <- object/tail@0.25 recall@3 / resident_share
    #   the unreachable 28.7       <- 100 - complete found@deep
    # They are computed into `allowed` by `_derived_figures()` below.
}


#: Ranges the record states as `A-B %` / `A to B points`. The scan's single-figure
#: alternative cannot see a range's LOWER bound at all (it is followed by an
#: en-dash, not a unit), so `3.7-51.0 %` could become `4.9-51.0 %` while only
#: `51.0` was checked — and an upper bound that happens to equal SOME artifact
#: rate passed membership while the statistic was wrong (#12186 review P2-5).
_DOC_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:–|-|\bto\b)\s*(\d+(?:\.\d+)?)\s*(?:%|pp\b|(?:percentage\s+)?points?\b)")

#: ⛔ The `A vs B` shape — a PROSE comparison. It usually carries no unit at all
#: (`recall@3 46.0 vs 46.3`), so `_DOC_FIGURE_RE` could not see either operand, and
#: this pair is the entire evidence of Finding 4 ("no static policy is a lever"):
#: re-staling it in the README AND the changelog left the suite green under the
#: previous scan (#12186 review P2-4).
_DOC_VS_RE = re.compile(
    r"(?<![\d.])(\d+\.\d)\s*(?:%|pp)?\s*vs\.?\s+(\d+\.\d)\s*(?:%|pp)?")


def _gap_endpoints(artifact: dict) -> list[str]:
    """The gaps the VERDICT rests on, computed — the source of `13.3 to 48.3`.

    ⛔ Derived, never read from `reproducibility.conclusions_rest_on_gap_pp`: a
    value that seeds its own allow-list cannot be wrong (#12186 review P2-2).
    """
    obj = artifact["populations"]["object"]["arms"]
    complete = obj["complete"]
    half = obj["tail@0.5"]
    gaps = [
        (complete["recall@3"] - half["recall@3"]) * 100,
        (complete["recall@15"] - half["recall@15"]) * 100,
        (complete["found_deep"] - half["found_deep"]) * 100,
        (half["misbind_at_1"] - complete["misbind_at_1"]) * 100,
    ]
    return [f"{min(gaps):.1f}", f"{max(gaps):.1f}"]


def _derived_figures(artifact: dict) -> set[str]:
    """Figures the artifact IMPLIES, computed rather than whitelisted."""
    out: set[str] = set()
    rep = artifact["reproducibility"]
    for key in ("found_deep_max_delta_pp", "recall_at_k_max_delta_pp",
                "tie_counting_max_delta_pp", "runs_compared"):
        out.add(f"{rep[key]:g}")
        out.add(f"{rep[key]:.1f}")
    low, _, high = rep["conclusions_rest_on_gap_pp"].partition(" to ")
    out |= {low, high}
    # the corpus mix, from the artifact's own harvest counts
    corpus = artifact["corpus"]["names_harvested"]
    total = sum(corpus.values())
    for cls in ("ident", "path"):
        out.add(f"{corpus[cls] / total * 100:.1f}")
    # the per-resident rate the README quotes for the tail arm at 0.25
    tail = artifact["populations"]["object"]["arms"]["tail@0.25"]
    out.add(f"{tail['recall@3'] / tail['resident_share'] * 100:.1f}")
    # the claim leg's unreachable share, complete
    out.add(f"{(1 - artifact['populations']['point']['arms']['complete']['found_deep']) * 100:.1f}")
    # ⛔ EVERY arm's drop from `complete`, on every gated metric, so the cost
    # ranges the record quotes ("the cold-tail arm loses 42.3 to 48.3, the random
    # control 42.0 to 50.3") are derived from the arms rather than whitelisted.
    # The peer-half of this set — recall_gap_vs_complete — only carries the
    # recall@k gaps; `found_deep` is a distance the record states too.
    for pop in ("object", "point"):
        arms = artifact["populations"][pop]["arms"]
        complete = arms["complete"]
        for arm, d in arms.items():
            if arm == "complete":
                continue
            for m in ("found_deep", "recall@3", "recall@5", "recall@10", "recall@15"):
                out.add(f"{abs(complete[m] - d[m]) * 100:.1f}")
    # ⛔ The tail-vs-random divergences the record quotes in either direction.    # A cross-arm difference is a figure the artifact IMPLIES just as much as the
    # vs-`complete` deltas above. The record now compares the two orders
    # explicitly — including where `tail` is AHEAD, which is what makes "never
    # buys back" false — so these are derived rather than whitelisted.
    for pop in ("object", "point"):
        arms = artifact["populations"][pop]["arms"]
        for cov in ("0.9", "0.75", "0.5", "0.25", "0.1"):
            for m in ("found_deep", "recall@3", "recall@5", "recall@10", "recall@15"):
                d = abs(arms[f"tail@{cov}"][m] - arms[f"random@{cov}"][m]) * 100
                out.add(f"{d:.1f}")
                out.add(f"{d:.2f}")
    return out


#: ⛔ A figure followed by a UNIT is a claim about the measurement whatever the
#: unit is spelled as, and the first cut of this scan only saw `%` (#12186 review
#: P0-2/P1-3). Measured then: rewriting `2.9 -> 0.9` and `Two runs -> Eleven
#: runs` in the README's reproducibility paragraph left the suite green, because
#: the band is quoted in `percentage points` and `points` carries no `%` at all —
#: so the two sentences a re-run most easily stales were the two nothing checked.
#: The integer alternative closes the same hole for `19-58 %`.
_DOC_FIGURE_RE = re.compile(
    r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|pp\b|(?:percentage\s+)?points?\b)")


def _arms_as_table_rows(arms):
    """The exact cell strings a README table must contain, derived from the artifact."""
    out = {}
    for arm, d in arms.items():
        rs = 100.0 if arm == "complete" else d["resident_share"] * 100
        out[arm] = {
            "resident": f"{rs:.1f}",
            "found@deep": f"{d['found_deep'] * 100:.1f}",
            "recall@3": f"{d['recall@3'] * 100:.1f}",
            "recall@5": f"{d['recall@5'] * 100:.1f}",
            "recall@10": f"{d['recall@10'] * 100:.1f}",
            "recall@15": f"{d['recall@15'] * 100:.1f}",
            "misbind@1": f"{d['misbind_at_1'] * 100:.1f}",
            "wrong-not-miss¹": f"{d['wrong_not_miss'] * 100:.1f}",
            "silent-zero¹": f"{d['silent_zero'] * 100:.1f}",
        }
    return out


def _parse_table(md, header_prefix):
    """[(arm, [cells...])] for the markdown table starting at `header_prefix`."""
    lines = md.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(header_prefix)), None)
    assert start is not None, f"no table starting {header_prefix!r}"
    header = [c.strip() for c in lines[start].strip("|").split("|")]
    rows, i = [], start + 2
    while i < len(lines) and lines[i].startswith("|"):
        cells = [c.strip().replace("**", "") for c in lines[i].strip("|").split("|")]
        rows.append((cells[0], dict(zip(header[1:], cells[1:], strict=True))))
        i += 1
    return rows


@pytest.mark.parametrize(
    "kind,header_prefix,columns",
    [
        ("object", "| arm | resident | found@deep | recall@3 | recall@5",
         ["resident", "found@deep", "recall@3", "recall@5", "recall@15",
          "misbind@1", "wrong-not-miss¹", "silent-zero¹"]),
        ("point", "| arm | resident | found@deep | recall@3 | recall@5 | recall@10 | recall@15",
         ["resident", "found@deep", "recall@3", "recall@5", "recall@10", "recall@15",
          "misbind@1", "wrong-not-miss¹", "silent-zero¹"]),
    ],
)
def test_every_result_table_cell_is_recomputed_from_the_artifact(
        artifact, kind, header_prefix, columns):
    """Each README cell must equal what the artifact computes — CELL BY CELL.

    ⛔ This replaced a substring-presence check (`assert "89.3" in readme`), which
    could not fail on a stale or misquoted cell and did not read the artifact at
    all. It also asserts the ROW SET, so an arm silently dropped from a table
    reds here rather than reading as "not measured".
    """
    readme = README.read_text(encoding="utf-8")
    rows = _parse_table(readme, header_prefix)
    arms = _arms(artifact, kind)
    expected = _arms_as_table_rows(arms)

    # the row SET must match: every arm the artifact holds is published, and no
    # phantom row appears.
    def norm(arm: str) -> str:
        """`tail@0.90` in the doc == `tail@0.9` in the artifact."""
        return arm.replace("0.90", "0.9").replace("0.10", "0.1")

    assert {norm(r[0]) for r in rows} == set(arms), (
        f"{kind}: table rows {sorted(norm(r[0]) for r in rows)} != artifact arms {sorted(arms)}")

    for arm, cells in rows:
        want = expected[norm(arm)]
        for col in columns:
            assert cells[col] == want[col], f"{kind}/{arm}/{col}: doc {cells[col]} != artifact {want[col]}"


def test_no_doc_states_a_percentage_the_artifact_does_not_hold():
    """Every `N.N %` in the record, §9.1 **and the changelog entry** is held.

    ⛔ This is the check that catches the defect the first review round found: a
    HEADING reading `89.0 % → 47.0 %` left behind by a re-run survived a
    substring check, because the body three lines below still contained the
    correct pair. Membership in the artifact's own figure set is the property
    that a stale number violates.

    ⛔ The CHANGELOG is scanned because `config/ci-surfaces.yml` lists it among
    the GUARDED artifacts — the paths that take the docs-only tier1 early return
    — and nothing read it (`grep -n CHANGELOG` on this module was empty). The
    `#12186` entry carries the same figures as the record and §9.1, so a
    docs-only edit staling one of them selected no surface and redded nothing:
    the #11739/#12295 silent-drop class the tier1 registration exists to close.
    """
    art = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    allowed = set(_NON_ARTIFACT_FIGURES) | _derived_figures(art)
    for pop in art["populations"].values():
        for d in pop["arms"].values():
            for _k, v in d.items():
                if isinstance(v, float):
                    allowed.add(f"{v * 100:.1f}")          # as a percentage
                    allowed.add(f"{abs(v) * 100:.1f}")
    # …plus the derived deltas the docs quote (a difference of two artifact rates
    # is a figure the artifact *implies*, so it is allowed where it is exact).
    for pop in art["populations"].values():
        for d in pop["arms"].values():
            for k in ("recall@3", "recall@15", "misbind_at_1"):
                complete = pop["arms"]["complete"][k]
                allowed.add(f"{abs(d[k] - complete) * 100:.1f}")

    readme = README.read_text(encoding="utf-8")
    arch_91 = ARCH.read_text(encoding="utf-8")
    start = arch_91.index("### 9.1 M2")
    end = arch_91.index("\n---", start)
    changelog = CHANGELOG.read_text(encoding="utf-8")
    # the entry is the LAST `##` block in the file, so slice to the end: a figure
    # appended below it is still scanned rather than silently out of scope.
    entry_start = changelog.index("## `#12186`")
    bad = []
    for name, text in (("README", readme), ("§9.1", arch_91[start:end]),
                       ("changelog #12186", changelog[entry_start:])):
        # the RANGES first, so their lower bound is checked too (P2-5)
        for m in _DOC_RANGE_RE.finditer(text):
            low, high = m.group(1), m.group(2)
            line = text[: m.start()].count("\n") + 1
            for end_ in (low, high):
                if end_ not in allowed:
                    bad.append(
                        f"{name}:{line} states the range {m.group(0)!r} and its "
                        f"bound {end_} is not a figure the artifact holds")
            if float(low) >= float(high):
                bad.append(f"{name}:{line} states {m.group(0)!r} — not a range")
        for m in _DOC_VS_RE.finditer(text):
            a, b = m.group(1), m.group(2)
            line = text[: m.start()].count("\n") + 1
            for op in (a, b):
                if op not in allowed:
                    bad.append(
                        f"{name}:{line} compares {m.group(0)!r} and the operand "
                        f"{op} is not a figure the artifact holds")
        for m in _DOC_FIGURE_RE.finditer(text):
            figure = m.group(1)
            if figure not in allowed:
                line = text[: m.start()].count("\n") + 1
                bad.append(f"{name}:{line} states {figure} ({m.group(0)!r})")
    assert not bad, (
        "these figures are stated with a unit but the artifact does not hold "
        "them — a stale number left behind by a re-run, or a number that was "
        "never the artifact's:\n  " + "\n  ".join(bad))


def test_the_stated_empty_range_is_the_artifact_range(artifact):
    """The one range with no table row behind it is pinned to min/max.

    `empty` is not a column in either published table, so the `3.7–51.0 %` the
    README quotes for `:Object` is guarded by nothing but this — the range scan
    above can only check that both ends are rates the artifact holds SOMEWHERE.
    """
    windowed = [a["empty"] for name, a in _arms(artifact, "object").items()
                if name != "complete"]
    readme = README.read_text(encoding="utf-8")
    assert f"{min(windowed) * 100:.1f}–{max(windowed) * 100:.1f} %" in readme, (
        f"the README no longer states the artifact's `:Object` `empty` range "
        f"({min(windowed) * 100:.1f}–{max(windowed) * 100:.1f} %)")


def test_s91_headline_series_is_pinned_per_position(artifact):
    """§9.1's most-quoted claim, pinned POSITION BY POSITION — not by membership.

    ⛔ The `N.N %` scan is a MEMBERSHIP test, so it cannot tell a correct binding
    from a figure that merely exists somewhere in the artifact — the weakness
    `26a4f501a5` names in its own message while fixing it only for the ceiling pin.
    §9.1's central sentences were pinned by NOTHING: measured at `ef4af0fb4b8`,
    mutating the series' last element to `57.1`, `88.3` to `89.0`, the misbind
    `41.0` to `46.9`, and the `:Point` pair's right-hand side to `52.7` EACH LEFT
    THE SUITE GREEN. The series carries no `%` sign, so the scan never saw it; the
    others are figures the artifact holds from OTHER arms, so membership could not
    have caught them either.

    Parse the sentences' own shape and compare each slot to the arm it names.
    ⛔ BOTH series are pinned. The first revision of this test matched only the
    `tail` series (the only one followed by "at coverage"), so the `random` series
    `91.7 / 76.3 / 49.7 / 25.3 / 12.0` was still invisible — mutating it to `92.7`
    reds nothing. The guard must cover every series the section publishes.
    """
    arch = ARCH.read_text(encoding="utf-8")
    start = arch.index("### 9.1 M2")
    section = arch[start:arch.index("\n---", start)]

    m = re.search(
        r"((?:\d+\.\d\s*/\s*){4}\d+\.\d)\s+at coverage\s+"
        r"((?:\d+\.\d\d\s*/\s*){4}\d+\.\d\d)",
        section,
    )
    assert m, "§9.1 no longer states the tail-arm series in the pinned shape"
    figures = [f.strip() for f in m.group(1).split("/")]
    coverages = [c.strip() for c in m.group(2).split("/")]
    assert coverages == ["0.90", "0.75", "0.50", "0.25", "0.10"], coverages

    obj = _arms(artifact, "object")
    for fig, cov in zip(figures, coverages, strict=True):
        data = obj[f"tail@{float(cov):g}"]
        assert fig == f"{data['found_deep'] * 100:.1f}", (
            f"§9.1 states {fig} at coverage {cov}; the artifact holds "
            f"{data['found_deep'] * 100:.1f}")
        # the sentence's CLAIM is that found@deep EQUALS the resident share
        assert fig == f"{data['resident_share'] * 100:.1f}", (
            f"{fig} != the resident share at coverage {cov}")

    # …and the `random` series, which the first revision of this test could not see
    m = re.search(
        r"`random` arms equal\s*\*{0,2}their own\*{0,2}\s*resident shares\s+"
        r"((?:\d+\.\d\s*/\s*){4}\d+\.\d)",
        section,
    )
    assert m, "§9.1 no longer states the random-arm series in the pinned shape"
    rand_figs = [f.strip() for f in m.group(1).split("/")]
    for fig, cov in zip(rand_figs, coverages, strict=True):
        data = obj[f"random@{float(cov):g}"]
        assert fig == f"{data['found_deep'] * 100:.1f}", (
            f"§9.1 states {fig} for random@{cov}; the artifact holds "
            f"{data['found_deep'] * 100:.1f}")
        assert fig == f"{data['resident_share'] * 100:.1f}", (
            f"random {fig} != the resident share at coverage {cov}")


def test_s91_arrow_pairs_are_derived_per_metric(artifact):
    """Each `X % -> Y %` in §9.1 must be THAT metric's complete/windowed pair."""
    arch = ARCH.read_text(encoding="utf-8")
    start = arch.index("### 9.1 M2")
    section = arch[start:arch.index("\n---", start)]
    obj = _arms(artifact, "object")
    pt = _arms(artifact, "point")

    m = re.search(
        r"recall@3 falls\s*\*{0,2}\s*(\d+\.\d) %\s*\u2192\s*\*{0,2}\s*(\d+\.\d) %",
        section)
    assert m, "§9.1 no longer states the recall@3 pair"
    assert m.group(1) == f"{obj['complete']['recall@3'] * 100:.1f}", m.group(1)
    assert m.group(2) == f"{obj['tail@0.5']['recall@3'] * 100:.1f}", m.group(2)

    m = re.search(
        r"rises\s*\*{0,2}\s*(\d+\.\d) %\s*\u2192\s*\*{0,2}\s*(\d+\.\d) %", section)
    assert m, "§9.1 no longer states the wrong-top-1 pair"
    assert m.group(1) == f"{obj['complete']['misbind_at_1'] * 100:.1f}", m.group(1)
    assert m.group(2) == f"{obj['tail@0.5']['misbind_at_1'] * 100:.1f}", m.group(2)

    # the :Point pair — must NOT be a cross-population figure; the doc wraps it
    m = re.search(
        r"`:Point`\).{0,80}?`found@deep` falls\s*\*{0,2}\s*(\d+\.\d)\s*%\s*\u2192\s*"
        r"\*{0,2}\s*(\d+\.\d)\s*%",
        section, re.S)
    assert m, "§9.1 no longer states the :Point found@deep pair"
    assert m.group(1) == f"{pt['complete']['found_deep'] * 100:.1f}", m.group(1)
    assert m.group(2) == f"{pt['tail@0.5']['found_deep'] * 100:.1f}", m.group(2)


def test_every_record_discloses_the_point_self_retrieval():
    """`:Point` queries are self-retrieval; the records must say so.

    ⛔ This is the single caveat a reader needs to interpret the `:Point` table:
    every query is a verbatim span of its OWN target document, so `recall@3` 15.7 %
    and `found@deep` 71.3 % are an UPPER bound on the leg's usefulness, not answer
    quality. The instrument declares it under "what is real and what is not";
    neither record did, and nothing red on the omission.
    """
    arch = ARCH.read_text(encoding="utf-8")
    start = arch.index("### 9.1 M2")
    section = arch[start:arch.index("\n---", start)]
    readme = README.read_text(encoding="utf-8")
    for name, text in (("§9.1", section), ("README", readme)):
        assert "SELF-RETRIEVAL" in text, (
            f"{name} does not disclose that :Point queries are self-retrieval")
        assert re.search(r"NOT\s+a\s+measure of answer quality", text), (
            f"{name} discloses the construction but not what it does NOT measure")
    assert "self-retrieval" in TOOL.read_text(encoding="utf-8")


def test_no_record_counts_the_degenerate_complete_arm_as_an_observation():
    """"0.0 in every arm" is misleading for `found_deep_of_demoted`.

    ⛔ `_rate(x, 0)` returns 0.0, so the `complete` arm's `found_deep_of_demoted`
    is 0.0 by a ZERO DENOMINATOR — it has no demoted queries — not by measurement:
    20 arms measured it, not 22. ⚠️ Deliberately scoped to the
    `found_deep_of_demoted` claim and not to "0.0 in every arm" in general:
    `:Point` `empty` legitimately IS 0.0 in every arm including `complete`.
    """
    arch = ARCH.read_text(encoding="utf-8")
    start = arch.index("### 9.1 M2")
    section = arch[start:arch.index("\n---", start)]
    readme = README.read_text(encoding="utf-8")
    chg = (ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE-CHANGELOG.md").read_text(encoding="utf-8")
    art_text = ARTIFACT.read_text(encoding="utf-8")
    for name, text in (("§9.1", section), ("README", readme), ("CHANGELOG", chg),
                       ("measurement.json", art_text)):
        # ⛔ Two revisions of this scan were too weak, each measured:
        #   * a 220-char window let the CORRECTIVE sentence that follows re-supply
        #     the word "windowed";
        #   * requiring the literal `0.0` missed `== 0` and the artifact's own
        #     `== 0.0 in every arm of both populations`, and the artifact was not
        #     read at all — so the degenerate claim survived in the PRIMARY
        #     artifact while this test was green.
        # So: bind on "in every <X> arm" regardless of how the zero is spelled, and
        # read the artifact too.
        for m in re.finditer(r"found_deep_of_demoted", text):
            claim = text[m.start(): m.start() + 260]
            # ⛔ Both halves are required. A bare `= 0` used ALGEBRAICALLY — e.g.
            # "all 11 arms, `complete` included, so `silent_zero` ≡ 0 and — given
            # `found_deep_of_demoted = 0` — `wrong_not_miss` ≡ 1" — is a correct
            # derivation, not an unscoped observation claim. Only a zero attached
            # to "in every" is the defect (an arm COUNT asserted as evidence).
            if "in every" not in claim:
                continue
            if not re.search(r"found_deep_of_demoted\D{0,12}?(?:(?:==?)|is|was)\s*\**\s*0(?:\.0)?\b", claim):
                continue
            # ⛔ `==`, `=` and `is` must ALL count: a single `=` is the most
            # natural prose spelling, and a two-token matcher would silently
            # skip it — measured: re-introducing
            # "`found_deep_of_demoted` = 0.0 in every arm of both populations"
            # left the suite green under `(?:==|is)`. `test_the_zero_scope_scan_`
            # `recognises_every_spelling` pins the matcher on its own.
            scoped = re.search(r"in every\s+\**windowed", claim)
            assert scoped, (
                f"{name} claims found_deep_of_demoted is zero in every arm without "
                f"scoping it to the windowed arms — the complete arm has no demoted "
                f"queries, so its zero is a degenerate denominator")


def test_s91_records_that_the_s10_reopen_trigger_fired():
    """§9.1 must not attribute its three shapes to §10, and must not bury the trigger.

    ⛔ §9.1 said "§10 names its shape" — §10 lists TRIGGERS, not shapes, so the
    citation pointed at text that does not say what was claimed. And §10 lists
    *"M2 showing a windowed graph loses retrieval quality beyond an accepted
    bound"* among what reopens §7; on this record that trigger is MET, and nothing
    in §7, §9.1, §10 or the changelog recorded it.
    """
    arch = ARCH.read_text(encoding="utf-8")
    start = arch.index("### 9.1 M2")
    section = arch[start:arch.index("\n---", start)]
    assert "§10 names" not in section, (
        "§9.1 attributes its three shapes to §10; §10 lists triggers, not shapes")
    assert "§10's reopen trigger has FIRED" in section, (
        "§9.1 must record that §10's reopen trigger has fired on this evidence")
    assert "reopened" in section
    tail = arch[arch.index("## 10. What would reopen this decision"):]
    assert "M2 showing a windowed graph loses retrieval quality" in tail, (
        "§10 no longer carries the trigger §9.1 cites")

    # ⛔ The same dead pointer also lived OUTSIDE §9.1 — in §7 ("arming
    # additionally needs a decision §10 names") and §1.5. §10 lists TRIGGERS, so a
    # decision attributed to it cannot be found there. Check the WHOLE document,
    # not just the section, or the repair cannot see the copies it left behind.
    assert "decision §10 names" not in arch, (
        "a decision is still attributed to §10, which lists triggers, not shapes — "
        "the shapes live in §9.1")
    assert re.search(r"§9\.1 names the\s+three shapes", arch), (
        "§7 must point at §9.1 for the shapes and §10 only for the trigger")


def test_the_tool_declares_the_invariants_it_actually_asserts():
    """The header must not claim an assertion `run()` does not make.

    ⛔ "FOUR INVARIANTS, ASSERTED" overstated it: number 4 (the seed-collision
    property) is a CONSTANT — `run()` applies `EVICT_SEED_OFFSET` as an offset and
    records it, which is not an assertion that the two seeds cannot collide. The
    record itself says so ("a constant in the instrument, and it is not counted
    among the invariants"). A header that overstates enforcement is how a real
    invariant gets dropped later with nobody noticing.
    """
    src = TOOL.read_text(encoding="utf-8")
    for bad in ("FOUR INVARIANTS, ASSERTED", "THREE INVARIANTS, ASSERTED\n"):
        assert bad not in src, f"the header claims {bad.strip()!r} without the qualifier"
    assert "THREE INVARIANTS, ASSERTED IN `run()`" in src
    assert "CONSTANT, NOT ASSERTED IN `run()`" in src, (
        "invariant 4 must be labelled a constant, not an assertion")


def test_the_cleanup_note_says_the_graphs_are_populated(artifact):
    """`reset()` empties only the last arm's graph per population — not all of them.

    ⛔ The COUNT is asserted, not the adjective. The first revision checked only
    that the word `POPULATED` appeared and that `graphs_dropped == 0`, so editing
    "20 of 22" to "18 of 22" stayed green — the one figure the note exists to
    correct was unenforced. And the formula first written here was arithmetically
    wrong: `params.coverages` holds SIX values (`1.0` included) while `run()` skips
    `coverage >= 1.0`, so `2 * (1 + policies * coverages) - 2` is 24, not 22.
    """
    cleanup = artifact["cleanup"]
    pop = artifact["params"]
    per_pop = len(_arms(artifact, "object"))
    created = len(artifact["populations"]) * per_pop
    assert cleanup["graphs_created"] == created, (cleanup["graphs_created"], created)
    assert cleanup["graphs_dropped"] == 0

    # the derived count the note must state: ONE graph emptied per population
    emptied = len(artifact["populations"])
    populated = created - emptied
    note = cleanup["note"]
    assert "POPULATED" in note, (
        "the cleanup note must say the surviving graphs are POPULATED; "
        "'nodes are cleared after each population' reads as 22 empty graphs")
    assert f"{populated} of {created}" in note, (
        f"the note states the wrong count; the artifact implies "
        f"{populated} of {created} still populated")
    # …and the note's own arithmetic must close, not just its total:
    # `coverages` includes 1.0, which `run()` skips, so the arm count per
    # population is 1 + policies x (len(coverages) - 1).
    assert per_pop == 1 + len(pop["policies"]) * (len(pop["coverages"]) - 1), (
        "per-population arm count != 1 complete + policies x WINDOWED coverages "
        "(coverages includes 1.0, which run() skips)")

    src = TOOL.read_text(encoding="utf-8")
    anchor = src.find('"note": "each arm is measured on its own graph')
    assert anchor != -1, "the tool no longer emits a cleanup note"
    region = src[anchor: anchor + 1100]
    assert "POPULATED" in region, "the tool still emits the misleading cleanup note"
    assert f"{populated} of {created}" in region, (
        "the tool's note states a count the artifact does not imply")


def test_the_tool_refuses_without_a_live_graph():
    """A figure measured on the embedded backend is not a figure (S3's rule)."""
    import os
    import subprocess

    env = {k: v for k, v in os.environ.items() if k != "TORTOISE_DB_URI"}
    env["TORTOISE_DB_URI"] = ""
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--points", "1"],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert proc.returncode == 3
    assert "REFUSED" in proc.stderr


# ---------------------------------------------------------------------------
# Finding 4's operands, bound POSITIONALLY.
#
# `test_no_doc_states_a_percentage_the_artifact_does_not_hold` is a MEMBERSHIP
# test: it asks whether a figure appears in the artifact's figure SET, so a
# re-stale whose replacement is another artifact figure stays green. Measured on
# the previous revision (P2-4 recurrence, reproduced independently):
#
#   "recall@3 **46.0 vs 46.3** at 0.50"  ->  "47.0 vs 46.3"   => 0 errors
#   "81.0 vs 81.7 at 0.90"               ->  "81.7 vs 81.0"   => 0 errors (inverted)
#   "up to **2.00 pp on `found_deep` at 0.50**" -> "at 0.75"  => 0 errors
#
# Every comparable figure in §9.1 got a positional pin; these operands did not,
# which is why the published bound could be guarded while the sentence stating it
# was not. This binds (tail, random, coverage) as a TRIPLE, so value, direction
# and position all have to be right at once.
# ---------------------------------------------------------------------------


def test_the_zero_scope_scan_recognises_every_spelling():
    """The de-scope scan must see `=`, `==` and `is` — a self-test on the guard.

    ⛔ Guards that cannot fail on their own defect are this PR's recurring failure.
    The scan's matcher was `(?:==|is)`, so the most natural prose spelling — a
    single `=` — was skipped entirely: re-introducing "`found_deep_of_demoted` =
    0.0 in every arm of both populations" left the suite GREEN. This asserts the
    matcher itself, on the strings the documents actually use.
    """
    pat = re.compile(r"(?:(?:==?)|is|was)\s*\**\s*0(?:\.0)?\b")
    unscoped = [
        "`found_deep_of_demoted` is 0.0 in every arm of both populations",
        "`found_deep_of_demoted` == 0 in every arm of both populations",
        "`found_deep_of_demoted` = 0.0 in every arm of both populations",
        "found_deep_of_demoted == 0.0 in every arm of both populations",
        "found_deep_of_demoted was 0 in every arm",
    ]
    for s in unscoped:
        assert pat.search(s), f"the scan would MISS this spelling: {s!r}"
        assert not re.search(r"in every\s+\**windowed", s), s
    # …and the correctly-scoped spellings still pass both halves
    for s in ("`found_deep_of_demoted` == 0 in every **windowed** arm",
              "found_deep_of_demoted is 0.0 in every **windowed** arm"):
        assert pat.search(s), s
        assert re.search(r"in every\s+\**windowed", s), s


def test_the_policy_comparison_operands_are_bound_to_their_cells(artifact):
    """Each published `A vs B at COV` pair must be the real cell — in EVERY record.

    ⛔ Parameterised over both documents. The first revision read ONLY the README,
    so §9.1 carried the same operands guarded by nothing but the membership set:
    measured green at 44 passed while `46.0 vs 46.3 at 0.50` was inverted to
    `46.3 vs 46.0` in §9.1 (the identical README mutation reds). A one-of-two-
    siblings guard is the defect class this suite exists to catch.
    """
    raw = README.read_text()
    obj = _arms(artifact, "object")

    # The Finding-4 paragraph: from its opening to the next rule.
    start = raw.index("`tail` (evict the least-referenced/most-boilerplate first)")
    body = raw[start:raw.index("---", start)]

    arch = ARCH.read_text(encoding="utf-8")
    a_start = arch.index("### 9.1 M2")
    body_91 = arch[a_start:arch.index("\n---", a_start)]

    # Every "A vs B **at COV**" — the pair bound to its OWN trailing coverage, so a
    # coverage token from a later clause cannot be borrowed by an earlier pair.
    bound = re.findall(r"(\d+\.\d)\s+vs\s+(\d+\.\d)\**\s+at\s+(0\.\d\d)", body)
    bound_91 = re.findall(r"(\d+\.\d)\s+vs\s+(\d+\.\d)\**\s+at\s+(0\.\d\d)", body_91)
    assert len(bound) == 4, bound
    assert bound_91, "§9.1 publishes no `A vs B at COV` pair — did the sentence move?"

    present = list(bound) + list(bound_91)
    assert present, "no `A vs B at COV` pair found in either record"

    # ⛔ Bind each pair to a REAL cell of a gated metric, in the RIGHT direction.
    # §9.1's pairs are not all `recall@3` — `51.7 vs 49.7 at 0.50` is `found_deep`
    # — and the reviewer measured that applying the recall@3 binding uniformly
    # false-reds. Matching against the metric SET keeps value and direction bound
    # (a swap matches no metric with the right direction) without forcing the test
    # to parse prose labels the section does not consistently carry.
    metrics = ("recall@3", "found_deep", "recall@5", "recall@10", "recall@15")
    for a, b, cov in present:
        k = str(float(cov))          # "0.50" -> "0.5"; arm keys are un-padded
        hits = [
            m for m in metrics
            if abs(float(a) - obj[f"tail@{k}"][m] * 100) < 0.05
            and abs(float(b) - obj[f"random@{k}"][m] * 100) < 0.05
        ]
        assert hits, (
            f"{a} vs {b} at {cov} is no real (tail, random) cell of any gated "
            f"metric — a swap or a cross-metric figure")
        # direction is part of the claim, not just the magnitudes
        for m in hits:
            tail = obj[f"tail@{k}"][m]
            rnd = obj[f"random@{k}"][m]
            assert (float(a) > float(b)) == (tail > rnd), (cov, m, a, b, tail, rnd)

    # The two "buys back" cells, each bound to its own population/arm/metric.
    assert "up to **2.00 pp on `found_deep` at 0.50**" in body
    d = (obj["tail@0.5"]["found_deep"] - obj["random@0.5"]["found_deep"]) * 100
    assert f"up to **{d:.2f} pp on `found_deep` at 0.50**" in body, d

    assert "2.33 pp on `:Point` `recall@10` at 0.90" in body
    pt = _arms(artifact, "point")
    d = (pt["tail@0.9"]["recall@10"] - pt["random@0.9"]["recall@10"]) * 100
    assert f"**{d:.2f} pp on `:Point` `recall@10` at 0.90**" in body, d

    # The two loss ranges, bound to their own ORDER — not merely present.
    # ⛔ `want in body` for each order separately is a MEMBERSHIP test: both range
    # strings appear under either labelling, so swapping them (or swapping the
    # order NAMES) stayed green while the document became false. Bind the whole
    # clause as one unit, so the label and the range cannot be detached.
    def _range(order):
        drops = [
            abs(obj["complete"][m] - obj[f"{order}@0.5"][m]) * 100
            for m in ("found_deep", "recall@3", "recall@5", "recall@10", "recall@15")
        ]
        return min(drops), max(drops)

    tail_lo, tail_hi = _range("tail")
    rand_lo, rand_hi = _range("random")
    # the two ranges must actually differ, or the binding below proves nothing
    assert (tail_lo, tail_hi) != (rand_lo, rand_hi), (tail_lo, tail_hi)

    clause = re.search(
        r"cold-tail arm loses\s+\*\*(\d+\.\d) to (\d+\.\d) points\*\*\s+"
        r"to the eviction and the random control\s+\*\*(\d+\.\d) to (\d+\.\d)\*\*",
        body,
    )
    assert clause is not None, "the loss-range clause is not in its expected form"
    got = tuple(float(x) for x in clause.groups())
    assert got == pytest.approx((tail_lo, tail_hi, rand_lo, rand_hi), abs=0.05), got


def test_the_record_quotes_the_current_wording_of_the_honesty_item():
    """The README quotes §7's honesty item — the quotation must be §7's CURRENT text.

    §7 used to say a windowed graph's retrieval cost was "unmeasured anywhere". This
    PR corrected that to "answered nowhere in that literature", because §9 IS the
    measurement: the absolute is refuted by the artifact it introduces. The README
    kept presenting the superseded sentence as "§7 states the honesty item plainly",
    so the record and the document it quotes contradicted each other — and nothing
    guarded it, because the doc scan reads figures with UNITS and this is a
    unit-less absolute in prose.

    Binding the quotation to its source is the fix: a future reword of §7 reds here
    instead of silently leaving the record quoting a retired claim.
    """
    def _norm(s: str) -> str:
        # markdown wraps mid-sentence and bolds across the break; compare prose, not
        # line breaks or emphasis markers
        return " ".join(s.replace("*", "").split()).lower()

    readme = README.read_text()
    arch = (ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md").read_text()

    clause = "whether a windowed graph harms retrieval quality is answered nowhere in that literature"
    assert clause in _norm(arch), "§7 no longer carries the corrected clause"
    assert clause in _norm(readme), (
        "the README does not quote §7's current wording of the honesty item"
    )

    # The retired absolute may appear ONLY as an explicitly retired quotation.
    if "unmeasured anywhere" in readme:
        assert "retired" in readme, (
            "the README still asserts the retired absolute 'unmeasured anywhere' "
            "without marking it as retired — §9 is that measurement"
        )


def test_every_published_arrow_is_bound_to_the_cell_it_reports(artifact):
    """Every published `A % -> B %` must equal the pair at ITS OWN arm and metric.

    `test_no_doc_states_a_percentage_the_artifact_does_not_hold` is a MEMBERSHIP
    test, and its docstring names the stale-heading case it is meant to catch — but
    membership is satisfied by any artifact figure from any arm or metric, so every
    arrow the README publishes was unpinned while §9.1's identical arrows were pinned
    (the one-of-two-siblings shape this PR has already fixed twice). Measured on the
    previous revision, each of these stayed GREEN at 46 passed:

        README heading      `88.3 % -> 46.0 %`  ->  `88.3 % -> 47.0 %`
        README              `71.3 % -> 41.0 %`  ->  `71.3 % -> 40.0 %`
        README              `27.7 % -> 41.0 %`  ->  `27.7 % -> 43.0 %`
        README     `87.1 % vs 100.0 %`  ->  `87.1 % vs 95.0 %`  (only operand 1 bound)
        §9.1        `nine of the ten`  ->  `eight of the ten`
        §1.5            `~11.7` / `0.410`  ->  `~12.7` / `0.420`

    The sibling mutation in §9.1 DID red, which is why the README half looked
    covered. This binds each fragment to a derivation, so the figure cannot drift
    without the derivation drifting with it.
    """
    def _ws(s: str) -> str:
        return " ".join(s.split())

    def pct(arms, arm, metric):
        return f"{arms[arm][metric] * 100:.1f}"

    obj = _arms(artifact, "object")
    pt = _arms(artifact, "point")
    readme = _ws(README.read_text())
    arch = _ws((ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md").read_text())

    # `:Object` recall@3, complete -> tail@0.5, and the misbind ladder.
    # the document uses a U+2212 MINUS SIGN, not a hyphen
    r3_drop = ("(\u2212"
               f"{obj['complete']['recall@3'] * 100 - obj['tail@0.5']['recall@3'] * 100:.1f}"
               " points)")
    fragments = [
        (readme, f"**{pct(obj,'complete','recall@3')} % → {pct(obj,'tail@0.5','recall@3')} %** and "
                 f"the wrong-answer rate at rank 1 rises "
                 f"**{pct(obj,'complete','misbind_at_1')} % → {pct(obj,'tail@0.5','misbind_at_1')} %**"),
        (readme, f"falls {pct(obj,'complete','recall@3')} % → {pct(obj,'tail@0.5','recall@3')} %"),
        (readme, f"recall@3 **{pct(obj,'complete','recall@3')} % → {pct(obj,'tail@0.5','recall@3')} %** "
                 f"{r3_drop}, recall@15 "
                 f"{pct(obj,'complete','recall@15')} % → {pct(obj,'tail@0.5','recall@15')} %"),
        (readme, f"`found@deep` falls **{pct(pt,'complete','found_deep')} % → "
                 f"{pct(pt,'tail@0.5','found_deep')} %**, and recall@3 "
                 f"{pct(pt,'complete','recall@3')} % → {pct(pt,'tail@0.5','recall@3')} %"),
        (readme, f"**{pct(obj,'complete','misbind_at_1')} % → {pct(obj,'tail@0.5','misbind_at_1')} %** "
                 f"(tail@0.50) → {pct(obj,'tail@0.25','misbind_at_1')} % (tail@0.25)"),
        (readme, f"{obj['tail@0.25']['recall@3'] / obj['tail@0.25']['resident_share'] * 100:.1f} % vs "
                 f"{obj['random@0.25']['recall@3'] / obj['random@0.25']['resident_share'] * 100:.1f} % "
                 f"at 0.25"),
    ]
    for doc, frag in fragments:
        assert frag in doc, f"published figure not bound to its cell: {frag!r}"

    # §9.1's "nine of the ten" is a COUNT over the windowed `:Point` arms, derived.
    windowed = [k for k in pt if k != "complete"]
    below = [k for k in windowed if pt[k]["found_deep"] < pt[k]["resident_share"]]
    coincident = [k for k in windowed if pt[k]["found_deep"] == pt[k]["resident_share"]]
    assert len(windowed) == 10, windowed
    assert len(below) == 9 and len(coincident) == 1, (len(below), coincident)
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
             6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten"}
    assert f"**{words[len(below)]} of the ten** windowed arms fall short" in arch
    assert coincident == ["random@0.1"], coincident

    # §1.5/§7's `:Point` ceiling term, derived from the same arm.
    headroom = (pt["tail@0.5"]["resident_share"] - pt["tail@0.5"]["found_deep"]) * 100
    frag = (f"~{headroom:.1f} points of it at `tail@0.5`, where `found@deep` is "
            f"{pt['tail@0.5']['found_deep']:.3f} against a resident share of "
            f"{pt['tail@0.5']['resident_share']:.4f}")
    assert frag in arch, f"§1.5/§7's ceiling term not bound: {frag!r}"
