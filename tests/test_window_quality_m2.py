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


def test_the_policy_does_not_protect_quality(artifact):
    """§9.1: the cold-tail order never BUYS quality back from a random control.

    ⛔ The claim is not equality. `tail` and `random` DO diverge where the
    resident set is small — an earlier cut of the record said "indistinguishable
    at every coverage", and this test asserted only the 0.50 cell, so it could
    not catch that the sentence was false. What the conclusion needs is that the
    two orders never separate by enough to change it: the divergence is a
    handful of points at every coverage, against gaps an order of magnitude
    larger.
    """
    for pop in ("object", "point"):
        arms = _arms(artifact, pop)
        for cov in ("0.9", "0.75", "0.5", "0.25", "0.1"):
            tail = arms[f"tail@{cov}"]["recall@3"]
            rand = arms[f"random@{cov}"]["recall@3"]
            # 6 points is generous against the observed spread and still far
            # below any divergence that would make "the policy is not a lever"
            # false: a re-ranking that bought back ~10 points reds this.
            assert abs(tail - rand) <= 0.06, (pop, cov, tail, rand)


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
    # the corpus mix (#11901's own audit, not this run) — DERIVED from
    # `corpus.names_harvested`, so it re-states the artifact rather than
    # inventing a figure: ident 12,557 / 156,449 = 8.03 %, path 5,314 = 3.40 %.
    "8.0", "3.4", "60", "30", "10", "22", "653", "786", "1000", "300", "11901",
    # the recall@3-realized-per-resident rate at object/tail@0.25 (0.2033/0.2333)
    "87.1",
    # §7's literature figure and §5's threshold
    "54", "1.0",
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
        ("point", "| arm | resident | found@deep | recall@3 | recall@15",
         ["resident", "found@deep", "recall@3", "recall@15", "misbind@1",
          "wrong-not-miss¹", "silent-zero¹"]),
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
    """Every `N.N %` in the record and §9.1 must be a figure the artifact holds.

    ⛔ This is the check that catches the defect the first review round found: a
    HEADING reading `89.0 % → 47.0 %` left behind by a re-run survived a
    substring check, because the body three lines below still contained the
    correct pair. Membership in the artifact's own figure set is the property
    that a stale number violates.
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
    bad = []
    for name, text in (("README", readme), ("§9.1", arch_91[start:end])):
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
