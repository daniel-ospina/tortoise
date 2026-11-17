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
import math
import re
import sys
from fractions import Fraction
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
    _signal_available,
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
    """The SELECTIVITY rule, on a fixture where the windows genuinely differ.

    ⛔ The first cut of this test could not fail (#13126 review Finding 2). Every
    term mapped to the same single document, so `_hits` returned 1 for all four
    candidate windows and the `min` was decided by the tie-break alone — and the
    assertion named the SET of all four windows, so `min` -> `max` and deleting
    the postings branch entirely both stayed green. The rule this instrument
    adopted on the strength of a measured 19.0 % -> 15.7 % ceiling comparison was
    therefore unpinned: a silent revert to the first-window rule changed the
    measured query set with the suite green.

    The fixture below makes the intersection sizes DIFFER, so the adopted rule has
    a distinct answer from both its neighbours:

      window                     intersection          size
      alpha beta gamma d eps     {0,1,2}..           3
      beta gamma d eps zeta      {0,1,2} n {0} =     1   <- most selective
      gamma d eps zeta eta       {0,1,2} n {0} =     1   (ties; earliest wins)
      d eps zeta eta theta       {0,1,2} n {0} =     1

    The expected span is asserted EXACTLY, and it is neither the first window
    (which is what `max` and a deleted postings branch both return) nor the last.
    """
    text = "alpha beta gamma delta epsilon zeta eta theta"
    postings = {
        "alpha": {0, 1, 2}, "beta": {0, 1, 2}, "gamma": {0, 1, 2},
        "delta": {0, 1, 2}, "epsilon": {0, 1, 2},
        # The second window's tail terms are held by ONE document, so the window
        # starting at `beta` intersects to a single doc while the first window
        # intersects to all three.
        "zeta": {0}, "eta": {0}, "theta": {0},
    }
    first_window = "alpha beta gamma delta epsilon"
    # Without postings the first window wins…
    assert query_span(text, n_tokens=5) == first_window
    # …and with them the most selective window wins, deterministically.
    assert query_span(text, postings=postings, n_tokens=5) == (
        "beta gamma delta epsilon zeta")
    assert query_span(text, postings=postings, n_tokens=5) != first_window
    assert query_span(text, postings=postings, n_tokens=5) == query_span(
        text, postings=postings, n_tokens=5)


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


def test_the_artifact_records_that_the_loss_carries_no_signal(artifact, monkeypatch):
    """§5 Q2 is violated by construction, and the record's claim rests on this.

    ⛔ The four claims that carry the "**and it is silent**" half of the verdict
    were read by NOTHING (#13126 round 4): starting `_fts_rows` to pass
    `read_status_out`, flipping `TORTOISE_RESIDENCY` on, marking `whole-graph` as
    SERVED in `record_serve`, or landing a §7 reason string in the tree each left
    52 green. The record's `signal` block is *supposed* to move when the code
    moves — the instrument's own docstring promises "Resolved from the CODE, not
    asserted, so a later change that wires a signal moves this field instead of
    leaving the doc claiming silence forever" — but the field cannot move, because
    the committed artifact is never regenerated and nothing reds. So the ARTIFACT
    is compared to what `_signal_available()` resolves TODAY: the day a signal is
    wired, this reds and the record is corrected in that PR.
    """
    assert artifact["signal"] == {
        "capture_prior_read_status": False,
        "capture_prior_truncation": False,
        "residency_marker_armed": False,
    }
    # ⛔ HERMETIC: `residency_marker_armed` is resolved from `TORTOISE_RESIDENCY`,
    # an AMBIENT deployment flag, so a developer shell with it set reded this pin
    # with "a signal has been wired" when no code had moved (#13126 round 5,
    # Finding 6/P3). What is pinned is the CODE's default (unarmed), so the
    # environment is fixed here rather than read from whoever runs the suite.
    from tortoise.residency import RESIDENCY_FLAG_ENV
    monkeypatch.delenv(RESIDENCY_FLAG_ENV, raising=False)
    assert artifact["signal"] == _signal_available(), (
        "the code now resolves a different silence state than the committed "
        "artifact records — with `" + RESIDENCY_FLAG_ENV + "` unset, a signal has "
        "been wired (or removed), so the record's 'and it is silent' half and "
        "§1.5's justification must be re-derived")
    # ⛔ …AND THE FIELD IS STILL BOUND TO THE FLAG. Fixing the ambient-env problem by
    # deleting the flag from the comparison is not a fix: with `residency_enabled()`
    # never read, `residency_marker_armed` could be hardcoded `False` and this test
    # stayed GREEN (measured: `1 passed` under exactly that mutant). Hermeticity and
    # binding are two requirements, so the ARMED arm is asserted too — the field must
    # MOVE when the flag moves, or nothing here reads the code that would end the
    # record's silence.
    monkeypatch.setenv(RESIDENCY_FLAG_ENV, "1")
    assert _signal_available()["residency_marker_armed"] is True, (
        f"`residency_marker_armed` did not move when {RESIDENCY_FLAG_ENV} was armed — "
        f"the silence this pin certifies is not read from the code that would end it")


def test_the_silence_claims_are_bound_to_the_code_that_makes_them():
    """The ARCHITECTURAL half of "and it is silent", which prose cannot keep true.

    ⛔ Same class, measured green under mutation (#13126 round 4): flipping
    `record_serve.ANCHORED_READS["whole-graph"]` to SERVED, or landing either of
    §7's two reason strings anywhere in the tree, redded nothing. §9.1's crux is
    that a demoted candidate has no record-served set read to repair from, and
    route 3 in the record prices the work as "the two §7 reason strings, the
    residency marker armed, and a record-served text read" — so a reader deciding
    whether to arm the window is reading these three facts.
    """
    import tortoise.record_serve as record_serve

    status = record_serve.ANCHORED_READS["whole-graph"]
    status = getattr(status, "status", status)
    assert status is record_serve.NOT_SERVED or status == record_serve.NOT_SERVED, (
        f"`whole-graph` is now {status!r} in record_serve — if a record-served set "
        f"read exists, §9.1's 'no set read to repair from' and the record's route 3 "
        f"both change; correct them in that PR")
    # …and the two reason strings §7 requires still appear in NO MODULE of the
    # tree. ⛔ The scan used to read `tortoise/` only, while the claim it pins is
    # tree-wide (README: "appear in no module of the tree"; §9.1 the same): a
    # reason string landed in `tools/` — the tree that SHIPS the instrument — and
    # the pin stayed green (#13126 round 5, Finding 4/P2). The scan is therefore
    # tree-wide over Python modules, and the only exclusion is THIS file, which
    # must name the strings in order to forbid them.
    reason_strings = ("truncated_boundary", "insufficient_graph")
    this_file = Path(__file__).resolve()
    skip_dirs = {".venv", ".git", "node_modules", ".worktrees", "build", "dist",
                 "__pycache__", ".mypy_cache", ".pytest_cache"}
    offenders = []
    for path in ROOT.rglob("*.py"):
        parts = path.relative_to(ROOT).parts
        if set(parts) & skip_dirs or any(p.startswith(".") for p in parts):
            continue
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(s in text for s in reason_strings):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, (
        f"a §7 reason string now appears in {sorted(offenders)} — the record's "
        f"Finding 5 ('the two reason strings appear in no module of the tree') is "
        f"stale, and §7's signal requirement is closer than the record says")


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
    """The three blocks are PRESENT and self-consistent — a DRIFT check, no more.

    ⛔ This is the pin `config/ci-surfaces.yml` claims and did not have (#12186
    review P1-1): the row said it pinned "the artifact's own `reproducibility` /
    `provenance` / `cleanup` blocks being present and self-consistent", and no
    test in this module read any of them. Measured then: setting
    `provenance.tool_sha256` to 64 zeros and `reproducibility.runs_compared` to 11
    left the suite green.

    ⛔ **AND IT IS NOT PROVENANCE EVIDENCE — that was the overclaim this
    docstring used to make (#13126 review Finding 1).** Both sides of assertions
    (1) and (2) are HAND-MAINTAINED: `REPRODUCIBILITY_BOUND` is a constant in the
    instrument and `provenance.tool_sha256` is rewritten in the same commit
    whenever the instrument changes (measured on this branch: seven rewrites of
    the hash while `generated_at` and `git_head` never moved). So the pin catches
    the two copies DIVERGING — a real defect, the one that shipped at #12186
    P0-1 — and it cannot tell whether a run produced the numbers. Nothing in the
    committed bundle can: the three compared runs' outputs are not committed.
    Per SLSA v1.2 build-requirements, provenance is evidence only when the
    PRODUCING run writes it; a digest maintained by hand beside its subject is a
    cosmetic consistency check, and that is what this test is. The record states
    the same thing in those words (see
    `test_the_record_does_not_present_its_digest_as_provenance`).
    """
    import hashlib

    tool = _load_tool()

    # (1) the block in the artifact IS the block the instrument emits.
    assert artifact["reproducibility"] == tool.REPRODUCIBILITY_BOUND, (
        "the artifact's `reproducibility` block is not the one the instrument "
        "composes — the two hand-maintained copies have diverged, so a reader "
        "cannot tell which one a reproducer would get back")

    # (2) the digest names the tool bytes as committed beside it.
    digest = hashlib.sha256(TOOL.read_bytes()).hexdigest()
    assert artifact["provenance"]["tool_sha256"] == digest, (
        "provenance.tool_sha256 no longer matches tools/window_quality_m2.py as "
        "committed — this is a DRIFT check, so update the digest in the SAME "
        "commit that edits the instrument. It is not evidence that a run "
        "produced the numbers; only re-running the tool can produce a new "
        "`reproducibility` band")

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


def test_the_record_does_not_present_its_digest_as_provenance():
    """The record states what its provenance block IS: a drift control, not evidence.

    ⛔ #13126 review Finding 1. The README claimed the artifact's
    `provenance.tool_sha256` was a binding "to the instrument's bytes" and that
    the block-equality pin made that binding "checked rather than asserted". Both
    halves are false, measurably: the digest is rewritten by hand in the same
    commit that edits `tools/` (seven times on this branch, while `generated_at`
    and `git_head` never moved), and the artifact-adding commit's instrument
    emitted a DIFFERENT `reproducibility` block (2 runs / 0.0 / 2.94, no
    `found_deep_max_delta_pp` at all) — so the block was written into the JSON, not
    by a run. A reader told to trust the band as measured would be trusting two
    hand-maintained copies of one another.

    This binds the CORRECTED sentences, so the overclaim cannot come back by a
    later edit: the false phrases must be gone and the honest ones present. It is
    a prose pin on purpose — the defect was prose, and the code pin next door
    (`test_the_artifact_binds_its_own_provenance`) can only ever check that the
    two hand-maintained copies agree.
    """
    body = _flat(README.read_text(encoding="utf-8"))

    # the two false claims, in the words they shipped in
    assert "binding these figures to the instrument's bytes" not in body, (
        "the README still presents provenance.tool_sha256 as binding the figures "
        "to the instrument — it is a hand-maintained digest of the CURRENT bytes")
    assert ("so the `provenance.tool_sha256` binding is checked rather than"
            " asserted") not in body, (
        "the README still claims the block-equality pin checks the provenance "
        "binding — it checks that two hand-maintained copies agree")

    # …and the statements that replaced them
    assert ("it names the **current** bytes and never the bytes that"
            " produced these numbers") in body, (
        "the record no longer says what the digest does name")
    assert ("the `reproducibility` block (3 runs, 0.33 / 1.0 / 9.85 pp) is a"
            " hand-maintained constant whose three source runs are **not**"
            " committed") in body, (
        "the record no longer discloses that the band's source runs are uncommitted")
    assert ("It is **not** provenance evidence") in body, (
        "the record no longer denies the provenance claim it used to make")


FILTERS = ("too_short", "no_span", "sanitised_out", "duplicate_span")


def _assert_point_build_accounting(pb: dict, *, requested: int,
                                   arms_nodes: int) -> None:
    """The funnel contract for ONE `corpus.point_build` block, both shapes.

    ⛔ TWO artifact shapes exist and BOTH must pass (#13126 round 5, Finding 1/P1):
    the COMMITTED block predates the accounting (`split_recorded: false`, the gap
    size in `unattributed`, plus `reason`/`causes`), and a run of the CURRENT
    instrument records the four filters and emits **no** `split_recorded` and
    **no** `unattributed` at all (`build_point_population`'s funnel). Branching on
    the flag therefore rejected a CORRECT artifact on the record's own documented
    regeneration path — the `KeyError` merely became an `AssertionError`, so the
    instrument→artifact→pin loop was still open.

    So the branch is keyed on the PROPERTY, not on a flag: a block records the
    split iff it carries all four filter counters. A flag, when present, must
    agree with that — a `split_recorded: true` over a block with no counters is a
    claim the block does not support.
    """
    # the request and the result are the artifact's own declared numbers, not
    # copies that may drift from them
    assert pb["requested"] == requested, (
        f"point_build.requested {pb['requested']} != params.points {requested}")
    assert pb["built"] == arms_nodes, (
        f"point_build.built {pb['built']} != populations.point.nodes {arms_nodes}")
    assert pb["requested"] >= pb["built"], "the builder cannot build more than asked"

    lost = pb["requested"] - pb["built"]
    has_split = all(f in pb for f in FILTERS)

    if "split_recorded" in pb:
        assert pb["split_recorded"] == has_split, (
            f"`split_recorded: {pb['split_recorded']}` but the four filter counts "
            f"are {'all' if has_split else 'NOT all'} present in {sorted(pb)} — the "
            f"flag and the counters disagree about whether the split was recorded")

    if has_split:
        assert sum(pb[f] for f in FILTERS) == lost, (
            f"the funnel accounts for {sum(pb[f] for f in FILTERS)} of the {lost} "
            f"texts lost — a drop is unaccounted for")
    else:
        # no split: the size of the gap must still be recorded, and explained
        assert pb.get("unattributed") == lost, (
            f"the block records neither the four filter counts {FILTERS} nor the "
            f"gap size: a run without the split must state {lost} in "
            f"`unattributed`, and it says {pb.get('unattributed')!r}")
        assert pb.get("reason", "").strip(), (
            "a run that did not record the split must say why, not leave it silent")
        assert set(pb.get("causes", ())) == set(FILTERS), (
            "the possible causes must be named, or 'unattributed' is unreadable")


def test_the_point_build_pin_accepts_the_block_the_current_instrument_emits():
    """⛔ The loop CLOSED, tested against the instrument's real output.

    #13126 round 5, Finding 1/P1. The previous round keyed the pin on
    `split_recorded`, a key the instrument does not emit, so every artifact the
    current instrument can produce failed the pin (`assert None == 114`), and the
    README's documented regeneration path led to a red on a CORRECT artifact.
    A hand-typed dict would restate the shape under test, so this asks
    `build_point_population` for it: eight distinct long texts plus two whose span
    is already claimed, and the funnel it fills in must satisfy the pin.

    Measured pre-fix: `AssertionError: the run did not record the split, so the
    gap size (2) must be stated in `unattributed`; it says None`.
    """
    long_tail = " and more words here to pass the eighty character gate"
    subjects = ["reservation window", "eviction order", "coverage sweep",
                "canonical phrase", "silent demotion", "prior read status",
                "truncation boundary", "anchor read"]
    verbs = ["holds closed components", "ranks least referenced",
             "measures resident share", "repeats verbatim",
             "labels every answer", "records the flag", "refuses a claim",
             "repairs the set"]
    texts = [f"the {subject} {verb} inside the window while the sweep proceeds"
             f"{long_tail}" for subject, verb in zip(subjects, verbs, strict=True)]
    texts += [texts[0], texts[1]]        # spans already claimed -> duplicate_span
    funnel: dict = {}
    pop = build_point_population(texts, len(texts), sanitise=lambda s: s,
                                 stats=funnel)

    # the shape this test exists to accept is the one WITHOUT the legacy keys
    assert "split_recorded" not in funnel and "unattributed" not in funnel, (
        f"the instrument now emits one of the legacy keys — the two shapes are no "
        f"longer what this test distinguishes: {sorted(funnel)}")
    assert funnel["duplicate_span"] == 2, funnel
    assert funnel["built"] == len(pop) == 8, funnel
    _assert_point_build_accounting(funnel, requested=len(texts),
                                  arms_nodes=len(pop))


def test_the_artifact_records_the_point_population_drop_funnel(artifact):
    """`--points 900` is a REQUEST; the 786 built and the 114 lost are accounted.

    ⛔ #13126 review Finding 2 / P2-3. The instrument said the drop count was
    "recorded by the caller's own accounting" and nothing counted it, so the only
    trace of 900 -> 786 was two unrelated fields (`params.points` and
    `populations.point.nodes`) that cannot distinguish the four filters which can
    lose a text. The `corpus.point_build` block is that accounting; this pin holds
    it to the rest of the artifact and to itself.

    ⛔ It does NOT invent the split for the committed run: that run predates the
    accounting, and the block says so (`split_recorded: false`). A pin that
    demanded four counts here would have to be satisfied by guessing them, which
    is the defect, not the fix. The rule itself lives in
    `_assert_point_build_accounting`, shared with the test that asks the CURRENT
    instrument for its block — one owner, so the two shapes cannot drift apart.
    """
    _assert_point_build_accounting(
        artifact["corpus"]["point_build"],
        requested=artifact["params"]["points"],
        arms_nodes=artifact["populations"]["point"]["nodes"])


def test_the_point_population_accounts_for_every_drop():
    """`build_point_population(stats=...)` reports the funnel and it ADDS UP.

    ⛔ #13126 review Finding 3: the accounting is what makes the artifact's block
    (and therefore the README's `:Point` disclosure) possible at all, so it is
    pinned on the object the instrument calls, not on a re-typed copy. Measured
    pre-fix: `stats` did not exist, the two docstrings pointed at a caller that
    passed nothing, and the 900 -> 786 shrink had no recorded cause.

    Three drops are exercised — one of each kind a long text can hit — plus the
    length gate, so every counter is a number some fixture produced rather than a
    key that happens to exist.
    """
    long_tail = " and more words here to pass the eighty character gate"
    ok = ("the reservation window holds closed components across the merge "
          "boundary" + long_tail)
    dup = ("a canonical phrase that repeats verbatim in this capture corpus"
           + long_tail)
    # `-- --- ### * * *` repeated has NO word-bearing 5-token run, so it yields
    # no span — and it is long enough to survive the 80-character gate, so it
    # exercises `no_span` rather than `too_short`.
    no_span = "-- --- ### * * * " * 6
    stats: dict = {}
    pop = build_point_population(
        ["too short to be a claim", ok, dup, dup, no_span], 10,
        sanitise=lambda s: s, stats=stats)

    # ⛔ The duplicate must not contribute a SECOND Point, and the two surviving
    # queries must be DISTINCT. The assertion here used to compare `pop` to
    # itself — an identity that cannot fail, in the one test whose subject is
    # `duplicate_span` (#13126 round 5, Finding 5/P3).
    queries = [r["query"] for r in pop]
    assert len(set(queries)) == len(queries), queries
    assert all(q.strip() for q in queries), queries
    # …and each query really is a span of the text that SURVIVED, so "distinct"
    # cannot be satisfied by a query the dropped text was discarded for.
    assert all(q in src for q, src in zip(queries, (ok, dup), strict=True)), queries
    assert len(pop) == 2, pop                       # `ok` and the first `dup`
    assert stats["requested"] == 10
    assert stats["texts_considered"] == 5
    assert stats["too_short"] == 1
    assert stats["kept"] == 4                       # the 80-character gate passed
    assert stats["no_span"] == 1
    assert stats["duplicate_span"] == 1
    assert stats["sanitised_out"] == 0
    assert stats["built"] == len(pop) == 2
    # THE INVARIANT: every kept text is built or lost to exactly one filter.
    assert stats["kept"] == (stats["built"] + stats["no_span"]
                             + stats["sanitised_out"] + stats["duplicate_span"])

    # …and the sanitiser filter is a real counter, not a dead key.
    collapsed: dict = {}
    build_point_population([ok], 10, sanitise=lambda s: "x y", stats=collapsed)
    assert collapsed["sanitised_out"] == 1 and collapsed["built"] == 0

    # …and omitting `stats` stays supported: the population is the contract.
    assert len(build_point_population([ok], 10, sanitise=lambda s: s)) == 1


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


def _flat(text: str) -> str:
    """One line: so a claim that wraps across lines is still one contiguous string."""
    return " ".join(text.split())


def _scope_bound(text: str, anchor: str, marker: str, window: int = 120) -> bool:
    """Is `marker` stated as part of the SAME claim as `anchor`?

    ⛔ A section-wide `marker in text` is not a scope check. Measured: `:Object`-only
    occurs TWICE in §9.1, so replacing `that equality is `:Object`-only` with `that
    equality holds in every arm` left the assertion satisfied by the OTHER
    occurrence — the false universal shipped green (#13126 review P1-1, second
    attempt). "Also present elsewhere" is not "present here": the marker has to sit
    inside the sentence that makes the claim.
    """
    flat = _flat(text)
    i = flat.find(anchor)
    if i < 0:
        return False
    j = flat.find(marker, i)
    return 0 <= j - i <= window


def test_the_object_only_scope_is_stated_wherever_the_identity_is(artifact):
    """The identity's SCOPE is the finding: the equality is a `:Object` law only.

    ⛔ Three repair rounds on this branch swapped one false universal for another
    (`b637f19649`, `df6f89741f`, `bdc0e1706e`), and NONE of them was guarded. The
    test above pins the artifact's topology and never reads a document, so the
    wording could go back to a universal with the suite green: replacing the
    README's "`:Object` identity, not a statement about the text leg as a whole"
    with "a universal identity", §9.1's "that equality is `:Object`-only" with
    "holds in every arm", and the changelog's "The identity is `:Object`-only"
    with "is universal", redded nothing — 46 passed (#13126 review P1-1).

    A claim stated more widely than the population that supports it is the exact
    error this measurement exists to correct, so the scope is pinned in every
    document that states the identity, from the artifact, not from the prose.
    """
    windowed = {a: v for a, v in _arms(artifact, "point").items() if a != "complete"}
    meets = sorted(a for a, v in windowed.items()
                   if v["found_deep"] == pytest.approx(v["resident_share"], abs=5e-4))
    short = [a for a, v in windowed.items()
             if v["found_deep"] < v["resident_share"] - 5e-4]
    assert meets == ["random@0.1"], meets
    assert len(short) == len(windowed) - 1, len(short)

    # The scope marker each document uses, bound to the CLAIM it scopes. ⛔ Two
    # spellings because the README states the scope as an identity CLASS and the
    # other two as `:Object`-only; one token for all three would have to be the
    # word "only", which the false universal ("holds in every arm") never uses.
    for name, text, anchor, scope in (
            ("README", README.read_text(encoding="utf-8"),
             "Cost equals the eviction share", "`:Object` identity"),
            ("§9.1", _section(ARCH.read_text(encoding="utf-8"), "### 9.1 M2"),
             "does NOT hold in every arm", "`:Object`-only"),
            ("changelog #12186", _section(CHANGELOG.read_text(encoding="utf-8"),
                                          "## `#12186`"),
             "The identity is", "`:Object`-only")):
        assert _scope_bound(text, anchor, scope), (
            f"{name} no longer scopes the identity to `:Object` at the claim that "
            f"states it ({anchor!r} … {scope!r}) — the exact false universal "
            f"three repair rounds removed")
        assert f"`{meets[0]}`" in text, (
            f"{name} no longer names the one `:Point` arm the equality coincides "
            f"at (`{meets[0]}`) — the exception is what makes the scope checkable")
    # The count of short arms, SPELLED in §9.1 — the figure scan cannot see it
    # (`eight of the ten` carries no unit), so `nine` could become `eight` green.
    spell = {7: "seven", 8: "eight", 9: "nine", 10: "ten", 11: "eleven"}
    arch_91 = _section(ARCH.read_text(encoding="utf-8"), "### 9.1 M2")
    assert f"**{spell[len(short)]} of the ten**" in arch_91, (
        f"§9.1 no longer states the derived short-arm count "
        f"({spell[len(short)]} of the ten; the artifact has {len(short)})")


def test_s7_states_the_point_ceiling_it_quotes(artifact):
    """§7 — the section that ARMS the window — publishes three M2 figures.

    ⛔ The figure scan slices §9.1, and a bare rate carries no unit for it to
    match on, so §7's own sentence was read by nothing: `~11.7 points`, `0.410`
    and `0.5267` could become `~99.9 points`, `0.430` and `0.5367` with all 46
    tests green (#13126 review P2-3). That sentence is the arming decision, and
    §7 is where a reader deciding whether to arm the window stops reading — the
    #11739/#12295 silent-drop class the tier1 registration exists to close.
    """
    arm = _arms(artifact, "point")["tail@0.5"]
    gap = (arm["resident_share"] - arm["found_deep"]) * 100
    s7 = _section(ARCH.read_text(encoding="utf-8"), "## 7. The window")
    for want, what in (
            (f"~{gap:.1f} points", "the `:Point` ceiling's own cost"),
            (f"{arm['found_deep']:.3f}", "the windowed `found@deep`"),
            (f"{arm['resident_share']:.4f}", "that arm's resident share")):
        assert want in s7, (
            f"§7 no longer states {what} ({want}) in its arming sentence — "
            f"re-derive it from the artifact rather than editing the number")


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
    # ⛔ The RATIO the record quotes, derived and then required of the prose. The
    # order test above is satisfied by any band below the floor, so the fraction
    # was unread: `~3/4 of that floor` could become `~1/8` — which contradicts the
    # sentence's own next clause, "*not* the 'barely 1/8' an earlier cut claimed" —
    # with all 46 tests green (#13126 review P2-4).
    ratio = rep["tie_counting_max_delta_pp"] / float(low)
    frac = Fraction(ratio).limit_denominator(4)
    assert ratio == pytest.approx(float(frac), abs=0.02), (
        f"the band is {ratio:.3f} of the floor, which is not near a simple "
        f"fraction — the record's stated ratio must be re-derived")
    assert f"~{frac.numerator}/{frac.denominator} of that floor" in README.read_text(encoding="utf-8"), (
        f"the README no longer states the derived ratio (~{frac.numerator}/"
        f"{frac.denominator}) of the tie band to the verdict's floor)")


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
    divergences = [
        (pop, cov, m, abs(_arms(artifact, pop)[f"tail@{cov}"][m]
                          - _arms(artifact, pop)[f"random@{cov}"][m]))
        for pop in ("object", "point")
        for cov in ("0.9", "0.75", "0.5", "0.25", "0.1")
        for m in metrics
    ]
    worst = max(d for _pop, _cov, _m, d in divergences)
    # ⛔ The published bound is DERIVED from the artifact, and the three documents
    # that publish it must state THIS number. `published_bound = 0.05` used to be
    # a literal while the docstring below claimed "the ceiling IS the number the
    # record publishes": editing the bound to **7.7 points** in the README, §9.1
    # AND the changelog left all 46 tests green (#13126 review P1-2), because the
    # membership scan only rejects a figure the artifact does not hold SOMEWHERE
    # and 7.7 is some other arm's rate. A number that seeds its own pin cannot be
    # wrong, so the prose is compared to the derived value instead.
    stated = f"{worst * 100:.1f}"
    assert f"within **{stated} points** of each other" in README.read_text(encoding="utf-8"), (
        f"the README no longer publishes the artifact's own bound ({stated} points)")
    arch_91 = _section(ARCH.read_text(encoding="utf-8"), "### 9.1 M2")
    assert f"at most {stated} points on any gated metric" in arch_91, (
        f"§9.1 no longer publishes the artifact's own bound ({stated} points)")
    assert f"at most {stated} points on any gated metric" in CHANGELOG.read_text(encoding="utf-8"), (
        f"the changelog no longer publishes the artifact's own bound ({stated} points)")
    # …and the FINDING's own ceiling, which is a statement about the WINDOW rather
    # than a copy of the prose: "a few percent", whatever a re-run measures.
    # 1e-9 absorbs float noise only: the measured maximum is exactly 5.00 pp
    # (object@0.25 recall@3), so the ceiling is tight rather than slack.
    assert worst <= 0.05 + 1e-9, worst
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


def _section(text: str, heading: str) -> str:
    """One `##`/`###` section: from its heading to the next `---` rule, or to EOF.

    ⛔ A HEADING is the only bound that survives editing, and every pin that reads
    a claim must name the section that CARRIES it — a whole-document substring
    check let §9.1 go on publishing a figure §7 had retired. The EOF fallback is
    load-bearing here: this changelog's `#12186` entry is the LAST block, so there
    is no `---` after it (the same reason its scan slices to the end rather than
    to a rule).
    """
    start = text.index(heading)
    end = text.find("\n---", start)
    return text[start:] if end == -1 else text[start:end]


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
    pair_re = re.compile(r"(\d+\.\d)\s+vs\s+(\d+\.\d)\**\s+at\s+(0\.\d\d)")
    bound = list(pair_re.finditer(body))
    bound_91 = list(pair_re.finditer(body_91))
    assert len(bound) == 4, [m.group(0) for m in bound]
    assert bound_91, "§9.1 publishes no `A vs B at COV` pair — did the sentence move?"

    present = list(bound) + list(bound_91)
    assert present, "no `A vs B at COV` pair found in either record"

    # ⛔ Bind each pair to a REAL cell of a gated metric, in the RIGHT direction.
    # §9.1's pairs are not all `recall@3` — `51.7 vs 49.7 at 0.50` is `found_deep`
    # — and the reviewer measured that applying the recall@3 binding uniformly
    # false-reds. Matching against the metric SET keeps value and direction bound
    # (a swap matches no metric with the right direction) without forcing the test
    # to parse prose labels the section does not consistently carry.
    #
    # ⛔ AND WHEN THE PROSE DOES NAME A METRIC, THAT LABEL IS PART OF THE CLAIM.
    # §9.1 used to read "(`found_deep` 51.7 vs 49.7 at 0.50) and **behind** where
    # the resident set is small (20.3 vs 25.3 at 0.25)" — the second pair is
    # `recall@3`, so a reader carrying `found_deep` forward got 23.3, not 20.3, and
    # nothing red because the metric-set match accepts ANY metric that fits. A
    # label the clause itself places immediately before the operands is therefore
    # required to be one of the metrics the pair actually matches.
    metrics = ("recall@3", "found_deep", "recall@5", "recall@10", "recall@15")
    label_re = re.compile(r"(recall@\d+|found_deep)[`*)\s]*$")

    def _effective_label(text: str, pos: int) -> str | None:
        """The metric a reader carries at `pos`: the label placed immediately
        before the operands, else the last one named in the SAME sentence.

        A bare pair in a clause sequence (`(found_deep 51.7 vs 49.7 …) and
        behind (20.3 vs 25.3 …)`) inherits the metric of the clause it continues,
        which is why deleting a label is a false statement rather than a
        formatting change.
        """
        immediate = label_re.search(text[max(0, pos - 40):pos])
        if immediate:
            return immediate.group(1)
        paragraph = text[:pos].split("\n\n")[-1]
        sentence = re.split(r"(?<=[.!?])\s+", paragraph)[-1]
        named = re.findall(r"recall@\d+|found_deep", sentence)
        return named[-1] if named else None
    for found in present:
        a, b, cov = found.group(1), found.group(2), found.group(3)
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
        # …and the metric a reader CARRIES at the operands must be one of them.
        # ⛔ `if named:` alone left the DELETION arm blind: removing the label
        # left the pair inheriting the previous clause's metric — the exact defect
        # this check exists for — and stayed green (#13126 round 5, Finding 3/P2).
        # So an absent label resolves to the last metric named in the SAME
        # sentence, and an inherited label has to match too.
        label = _effective_label(found.string, found.start())
        if label:
            assert label in hits, (
                f"{a} vs {b} at {cov} reads as `{label}` (named beside the "
                f"operands, or inherited from the same sentence), but those "
                f"operands are the cell of {hits} — the label sends a reader to "
                f"the wrong metric")

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

    # ⛔ `at the target size` is REQUIRED, not optional. The group used to be
    # `(?:\s+at the target size)?`, so the qualification the round added to the
    # README was bound by nothing and deleting it stayed green — reverting the
    # prose to the flat "loses N points to the eviction" sentence this commit
    # exists to fix (#13126 round 5, Finding 2/P2). Observed pre-fix: delete it
    # from `README.md:280` -> 56 passed.
    clause = re.search(
        r"cold-tail arm loses\s+\*\*(\d+\.\d) to (\d+\.\d) points\*\*\s+"
        r"to the eviction\s+at the target size\s+and the random control\s+"
        r"\*\*(\d+\.\d) to (\d+\.\d)\*\*",
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


def test_every_quoted_figure_is_bound_to_its_own_arm(artifact):
    """Each figure the three documents quote is compared to a DERIVATION, not to a SET.

    ⛔ `test_no_doc_states_a_percentage_the_artifact_does_not_hold` is a MEMBERSHIP
    test: it rejects a figure no arm holds and ACCEPTS any figure some arm holds.
    So a plausible number lifted from a DIFFERENT arm — a different population, the
    other policy, the wrong coverage — passes, and the sentence then states the
    opposite of its own paragraph while the suite is green. Four review rounds have
    each found another instance (#13126 rounds 1-3), every one measured:

        README/CHANGELOG `:Object` series  8.7 -> 57.1                      green
        README/§9.1 tie band               9.85 pp -> 0.33 pp              green
        all three `:Point` scope pair      52.7/41.0 -> 49.3/40.0          green
        all three complete `misbind@1`     93.0 % -> 94.3 %                green
        §9.1 coincidence / ceiling         0.11 -> 0.21, 0.7133 -> 0.6133  green
        README bundle header               git head + all four corpus counts green

    This is deliberately one enumerated table rather than a rewrite of the scan: a
    binding is a property of a SENTENCE (which arm and metric does this number
    report?), and no general rule can recover that from prose. Adding a quoted
    figure means adding a row — which is why the residual is stated here instead of
    being claimed closed.
    """
    obj = _arms(artifact, "object")
    pt = _arms(artifact, "point")
    covs = ("0.9", "0.75", "0.5", "0.25", "0.1")
    readme = _flat(README.read_text(encoding="utf-8"))
    arch = _flat(ARCH.read_text(encoding="utf-8"))
    changelog = _flat(CHANGELOG.read_text(encoding="utf-8"))
    corpus = artifact["corpus"]["corpus"]
    rep = artifact["reproducibility"]

    def series(arms, key):
        return " / ".join(f"{arms[f'tail@{c}'][key] * 100:.1f}" for c in covs)

    def rseries(arms, key):
        return " / ".join(f"{arms[f'random@{c}'][key] * 100:.1f}" for c in covs)

    tail, rand = series(obj, "found_deep"), rseries(obj, "found_deep")
    # the identity holds before it is quoted: both series ARE resident shares
    assert tail == series(obj, "resident_share")
    assert rand == rseries(obj, "resident_share")

    half = pt["tail@0.5"]
    scope_share = f"{half['resident_share'] * 100:.1f}"
    scope_found = f"{half['found_deep'] * 100:.1f}"
    tie = f"{rep['tie_counting_max_delta_pp']:g}"
    misbind = f"{pt['complete']['misbind_at_1'] * 100:.1f}"
    coinc = f"{pt['random@0.1']['found_deep']:.2f}"
    ceiling = f"{pt['complete']['found_deep']:.4f}"
    plural = "s" if corpus["parse_errors"] != 1 else ""
    bundle = (f"{corpus['files']}/{corpus['files_contributed']} files, "
              f"{corpus['lines']} lines, {corpus['parse_errors']} parse error{plural}")

    # ⛔ The MODE bounds the whole record: every figure here is the lexical leg's,
    # and §7's arming decision covers both legs. Mutating it to `hybrid` in the
    # README AND §9.1 left 51 green (#13126 round 3) — a reader of §7 then believes
    # the fused production read was measured and the vector leg cleared.
    mode = artifact["retrieval"]["mode"]
    assert artifact["retrieval"]["has_embedder"] is False, artifact["retrieval"]
    assert artifact["retrieval"]["legs_seen"]["vector"] is False, artifact["retrieval"]
    # the OTHER three legs DID run, which the header must not understate: a reader
    # deciding whether route 1 ("move the text index") is enough needs to know the
    # candidate set also carried a graph-side kind-scan the move does not shift.
    legs = artifact["retrieval"]["legs_seen"]
    assert legs == {"fallback": True, "fts": True, "structural": True, "vector": False}, legs

    # the three tail-vs-random cells the README contrasts, in ITS order (tail first)
    trio = ", ".join(
        f"{obj[f'tail@{c}']['found_deep'] * 100:.1f} vs "
        f"{obj[f'random@{c}']['found_deep'] * 100:.1f}" for c in ("0.75", "0.5", "0.1"))
    # Finding 4's own magnitude at 0.25, which is NOT the published tie-band bound
    sep = abs(obj["tail@0.25"]["recall@3"] - obj["random@0.25"]["recall@3"]) * 100
    # the silent-loss pair — swapping the two policies stated the larger loss as
    # the cold-tail order's, contradicting the cell-bound `:Object` table
    sil_t = f"{obj['tail@0.5']['silent_zero'] * 100:.1f}"
    sil_r = f"{obj['random@0.5']['silent_zero'] * 100:.1f}"
    unreachable = f"{(1 - pt['complete']['found_deep']) * 100:.1f}"
    # the reproducibility restatements: a WORD for the run count, and §9.1's own
    # ceiling for the tie band (a manual round, not `f"{...:.1f}"` = 9.8)
    words = {2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    runs_word = words[rep["runs_compared"]]
    tie_ceiling = math.ceil(rep["tie_counting_max_delta_pp"] * 10) / 10
    pt_arms, obj_arms = _arms(artifact, "point"), _arms(artifact, "object")
    windowed_total = 2 * (len(pt_arms) - 1)                  # 20 windowed arms
    total_arms = len(pt_arms) + len(obj_arms)                 # 22 with `complete`
    gap_low, gap_high = _gap_endpoints(artifact)
    params = artifact["params"]
    pb = artifact["corpus"]["point_build"]

    # ⛔ The quantity the record NOMINATES as load-bearing — "the strong form of
    # (1) and the one the verdict rests on" — is quoted in five places and was
    # bound by nothing: `0.0` -> `0.1` everywhere left 52 green (#13126 round 4).
    # The artifact cannot move (a dedicated test pins it), so a stale PROSE copy
    # was the whole exposure, and it reads as "the window leaks" rather than
    # "the window costs".
    demoted_rates = {
        a["found_deep_of_demoted"]
        for arm, a in list(obj.items()) + list(pt.items())
        if arm != "complete"
    }
    assert demoted_rates == {0.0}, demoted_rates
    demoted = f"{0.0:.1f}"
    # the `:Point` `empty` census, in the two spellings the other documents use
    pt_arms_n = len(pt_arms)
    pt_population = artifact["populations"]["point"]

    # ⛔ `unattributed` exists ONLY in the legacy block shape, so the sentence that
    # binds it is selected BEFORE the row list is built (see the row's own comment:
    # evaluating it eagerly made the regeneration path raise `KeyError`).
    if "unattributed" in pb:
        pb_gap_row = (
            readme, f"with the {pb['unattributed']} lost texts left `unattributed`",
            "corpus.point_build unattributed (= requested - built)")
    else:
        pb_gap_row = (
            readme, f"the {pb['requested'] - pb['built']} lost texts",
            "corpus.point_build requested - built")
    rows = [
        # (document, fragment as the document states it, what it must derive from)
        (readme, tail, "the `:Object` tail series (found@deep, coverage 0.9→0.1)"),
        (readme, rand, "the `:Object` random series (its own resident shares)"),
        (changelog, tail, "the `:Object` tail series"),
        (changelog, rand, "the `:Object` random series"),
        (readme, f"up to **{tie} pp**", "tie_counting_max_delta_pp"),
        (arch, f"is {tie} pp, stated in the record", "tie_counting_max_delta_pp"),
        (readme, f"keeping {scope_share} % finds {scope_found} %",
         "point/tail@0.5 resident_share -> found_deep"),
        (arch, f"keeping {scope_share} % of claims finds only {scope_found} %",
         "point/tail@0.5 resident_share -> found_deep"),
        (changelog, f"keeping {scope_share} % finds only {scope_found} %",
         "point/tail@0.5 resident_share -> found_deep"),
        (readme, f"{misbind} % `misbind@1`", "point/complete misbind_at_1"),
        (arch, f"{misbind} % `misbind@1`", "point/complete misbind_at_1"),
        (changelog, f"{misbind} % `misbind@1`", "point/complete misbind_at_1"),
        (arch, f"merely **coincides** at {coinc}", "point/random@0.1 found_deep"),
        (arch, f"the complete graph is {ceiling} against 1.0",
         "point/complete found_deep"),
        (readme, f"**git head:** `{artifact['git_head'][:12]}`", "artifact.git_head"),
        (readme, f"**Corpus:** `~/.tortoise/session-events/*.jsonl` — {bundle}",
         "artifact.corpus.corpus counts"),
        # ── round 3 ────────────────────────────────────────────────────────
        (readme, f"**Retrieval mode:** `{mode}`", "artifact.retrieval.mode"),
        (arch, f'`retrieval.mode = "{mode}"`', "artifact.retrieval.mode"),
        (readme, f"(not the tail series above — {trio})",
         "object found_deep pair at 0.75/0.5/0.1"),
        (readme, f"separates by **{sep:.1f} points** at coverage 0.25",
         "object tail@0.25 minus random@0.25 recall@3"),
        (readme, f"the {sil_t} % of demoted entity queries",
         "object/tail@0.5 silent_zero"),
        (readme, f"({sil_r} % under `random@0.5`)", "object/random@0.5 silent_zero"),
        (readme, f"{unreachable} % of claim canonicals are unreachable",
         "1 - point/complete found_deep"),
        (readme, f"**{runs_word}** runs of identical input were compared",
         "artifact.reproducibility.runs_compared"),
        (arch, f"across {runs_word.lower()} runs of identical input",
         "artifact.reproducibility.runs_compared"),
        (arch, f"`recall@k` by at most {rep['recall_at_k_max_delta_pp']:g}",
         "artifact.reproducibility.recall_at_k_max_delta_pp"),
        (arch, f"the tie-counting rates by at most {tie_ceiling:g}",
         "artifact.reproducibility.tie_counting_max_delta_pp (ceiling, not a rounding)"),
        (arch, f"the gaps the verdict rests on run {gap_low} to {gap_high} points",
         "_gap_endpoints(artifact)"),
        (readme, f"all {len(pt_arms)} arms, `complete` included",
         "the `:Point` arm count"),
        (readme, f"{windowed_total} arms measured this, not {total_arms}",
         "windowed arms across both populations vs total"),
        (arch, f"`:Point` `empty` is {pt['complete']['empty']:.1f} in every arm",
         "point/complete empty"),
        (readme,
         f"--points {params['points']} --objects {params['objects']} "
         f"--sample {params['sample']}",
         "artifact.params (the reproduction recipe)"),
        # ── round 4: what the run did NOT record, and what the digest is ────
        (readme, f"{pb['requested']} texts are taken into the population builder "
                 f"and {pb['built']} Points come out",
         "corpus.point_build requested -> built"),
        # ⛔ SHAPE-AWARE, and built LAZILY. `unattributed` exists only in the LEGACY
        # block shape; a run of the CURRENT instrument records the four filters and
        # writes no such key. Filtering the row out AFTER this list is built did not
        # help: the f-string below is evaluated while the LIST LITERAL is constructed,
        # so the documented regeneration path still died with `KeyError: 'unattributed'`
        # (#13126 round 5, Finding 1/P1 — re-measured on the shape the instrument emits:
        # `1 failed, 56 passed`, `KeyError` at the removed filter's line). The row is
        # therefore SELECTED, not filtered, and each shape binds its own sentence.
        pb_gap_row,
        (readme, "It is **not** provenance evidence",
         "the provenance block is a drift control, not production evidence"),
        (readme, f"--seed` ({params['seed']})",
         "artifact.params.seed (the recipe's own seed)"),
        (readme, "plus the structural kind-scan",
         "retrieval.legs_seen.structural (the header must state the LEG SET)"),
        # ⛔ The artifact records `legs_seen.fallback = true`, which is "the tier
        # RAN" — and the record says so itself (Finding 6: "it is not a record of
        # the tier serving anything"). The header said "serving some queries",
        # which the artifact cannot support: `legs_seen` is four booleans, the
        # instrument drops the per-entry `count`, and the `:Object` arms' 3.7-51.0 %
        # `empty` shows the `count: 0` path firing. A reader judging route 1 would
        # take a graph-bound tier (built by `MATCH (n:Point)`) as a serving leg the
        # text-index move does not shift.
        (readme, "and the degraded snapshot tier was **reached**",
         "retrieval.legs_seen.fallback (RAN, not served)"),
        (readme, "at any rank **of the depth-50 read**",
         "the read the ranking claim is bounded to"),
        # ── round 4 ────────────────────────────────────────────────────────
        (readme, f"`found_deep_of_demoted` is **{demoted}** in every **windowed**",
         "every windowed arm's found_deep_of_demoted"),
        (readme, f"so its {demoted} is arithmetic, not",
         "the complete `:Point` arm's zero denominator"),
        (arch, f"`found_deep_of_demoted` is {demoted} in every **windowed** arm",
         "every windowed arm's found_deep_of_demoted"),
        (arch, f"so its {demoted} is a zero denominator",
         "the complete arm's zero denominator"),
        (changelog, f"`found_deep_of_demoted` is {demoted} in every **windowed**",
         "every windowed arm's found_deep_of_demoted"),
        (readme, f"`:Point` `empty` is **{pt['complete']['empty']:.1f} in",
         "point/complete empty"),
        (changelog, f"`empty` is {pt['complete']['empty']:.1f} in all {pt_arms_n} arms",
         "point/complete empty and the `:Point` arm count"),
        (changelog,
         f"{obj['tail@0.5']['recall@3'] * 100:.1f} vs "
         f"{obj['random@0.5']['recall@3'] * 100:.1f} at 0.50",
         "object tail@0.5 vs random@0.5 recall@3 (the changelog's own copy)"),
        (changelog, f"{pt_population['nodes']} `:Point` claims",
         "artifact.populations.point.nodes"),
    ]
    # the README quotes `signal` verbatim, and it is the evidence for half the
    # verdict ("— and it is SILENT"). Nothing compared the quote to the artifact,
    # so `"residency_marker_armed": true` in the fence left 51 green (#13126
    # round 3): a record whose own block says the marker IS armed tells a reader
    # the loss is already labelled.
    assert json.dumps(artifact["signal"]) in README.read_text(encoding="utf-8"), (
        "the README's quoted `signal` block no longer matches artifact['signal'] — "
        "the 'and it is silent' half of the verdict rests on it")

    missing = [f"{what}: {frag!r}" for doc, frag, what in rows if frag not in doc]
    assert not missing, (
        "these quoted figures no longer match the arm they report — either the "
        "artifact moved and the prose did not, or the figure was taken from a "
        "DIFFERENT arm (membership cannot tell the two apart):\n  "
        + "\n  ".join(missing))


def test_the_evictor_still_does_not_invalidate_the_degraded_snapshot():
    """The record's `#13123` claim is about CODE THIS PR DOES NOT CHANGE.

    ⛔ Measured: `grep -c 'fallback_snapshot\\|cache_evictor\\|record_serve'` on this
    module was **0** (#13126 round 3), so the claim was true at this head purely
    by luck of the tree. The verdict paragraph tells a reader the loss is silent
    TODAY because `cache_evictor._delete_nodes` issues a bare `DETACH DELETE` and
    never invalidates `fallback_snapshot` — the day `#13123` lands, that callout
    is false and nothing reds. This pin makes the record red in that PR instead of
    letting it rot green, the same binding `test_the_record_quotes_the_current_
    wording_of_the_honesty_item` already does for §7's prose.
    """
    src = (ROOT / "tortoise" / "cache_evictor.py").read_text(encoding="utf-8")
    assert "_delete_nodes" in src, (
        "the evictor's delete path was renamed — re-derive the record's #13123 "
        "claim rather than editing this pin")
    delete_path = src[src.index("def _delete_nodes"):]
    delete_path = delete_path[:delete_path.find("\ndef ", 10) if "\ndef " in delete_path[10:] else len(delete_path)]
    for token in ("fallback_snapshot", "_mark_dirty", "invalidate"):
        assert token not in delete_path, (
            f"`cache_evictor._delete_nodes` now mentions {token!r} — #13123 may have "
            f"landed, which makes the record's 'the loss is silent' callout stale; "
            f"correct the record in that PR and then relax this pin")


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


def test_the_claim_ceiling_pair_and_both_found_deep_series_are_bound(artifact):
    """Two series the suite could not red on, both the one-of-two-siblings shape.

    (1) The `:Point` "eviction + ceiling" pair `52.7 % / 41.0 %` is the whole evidence
    of Finding 2's claim half AND the quantity §1.5/§7's "~11.7 points" is computed
    from. It appears in three documents. Nothing read it: the test that calls binding
    "this test's whole job" asserts the value in the ARTIFACT, never the quote, and the
    §1.5/§7 pin binds the other spelling (0.410 / 0.5267). Measured on the previous
    revision: `41.0 -> 40.0` in the README stayed GREEN at 47 passed while the sentence
    became false and the derived headroom silently became 12.7.

    (2) The `:Object` `found@deep` series for BOTH orders is published in the README and
    the changelog, and pinned for §9.1 only. Measured: replacing the README's `random`
    series with the `tail` series stayed GREEN at 47 passed, leaving the paragraph
    contradicting itself one line later ("…not the tail series above").

    Both are bound to their own arm and order here, so a series cannot be swapped for
    its sibling — the failure the other order's numbers make invisible.
    """
    def _ws(s: str) -> str:
        return " ".join(s.split())

    obj = _arms(artifact, "object")
    pt = _arms(artifact, "point")
    covs = ("0.9", "0.75", "0.5", "0.25", "0.1")

    readme = _ws(README.read_text())
    arch = _ws((ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md").read_text())
    changelog = _ws(
        (ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE-CHANGELOG.md").read_text()
    )

    # (1) the `:Point` ceiling pair, in all three documents, derived from the arm.
    keep = f"{pt['tail@0.5']['resident_share'] * 100:.1f}"
    find = f"{pt['tail@0.5']['found_deep'] * 100:.1f}"
    # ⛔ The PHRASE, not the two numbers: `"41.0 %"` also occurs in the README's
    # `71.3 % → 41.0 %` arrow, so asserting the figure alone is membership again —
    # measured: `41.0 -> 40.0` here stayed GREEN at 50 passed until this changed.
    for name, doc, phrase in (
        ("README", readme, f"(keeping {keep} % finds {find} %)"),
        ("§9.1", arch, f"keeping {keep} % of claims finds only {find} %"),
        ("changelog", changelog, f"keeping {keep} % finds only {find} %"),
    ):
        assert phrase in doc, (name, phrase)
    # …and the pair must be the one the derived headroom is computed from.
    assert abs(
        (pt["tail@0.5"]["resident_share"] - pt["tail@0.5"]["found_deep"]) * 100
        - (float(keep) - float(find))
    ) < 0.06

    # (2) both `:Object` found@deep series, each bound to its own ORDER.
    def series(order):
        return " / ".join(f"{obj[f'{order}@{c}']['found_deep'] * 100:.1f}" for c in covs)

    tail_s, rand_s = series("tail"), series("random")
    assert tail_s != rand_s, (tail_s, rand_s)      # else the swap below is undetectable
    for name, doc in (("README", readme), ("changelog", changelog)):
        assert tail_s in doc, (name, "tail series absent", tail_s)
        assert rand_s in doc, (name, "random series absent", rand_s)
    # the README states the random series a second time, separately; it must be the
    # random one there too, not the tail series repeated.
    assert f"The `random` arms read {rand_s}" in readme
    assert f"the `random` arms read their **own** shares {rand_s}" in changelog
