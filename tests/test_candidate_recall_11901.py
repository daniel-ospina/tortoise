"""Pins the structural facts #11901's measurement rests on, and its artifact.

The brief (`docs/research/2026-11-12-11901-candidate-recall/research-brief.md`)
publishes numbers that a reader will quote. Two ways that record rot, and this
module closes both:

1. **The mechanism drifts out from under the prose.** The brief's claims are
   about the CODE — one query per entity name, one id-keyed union across those
   queries, an exact-`_norm` matcher — so each is asserted against the real
   functions here. If one changes, this reds instead of the document quietly
   becoming false.
2. **The tables drift away from the artifact.** Every number in the brief's
   tables is recomputed from the committed `measurement.json` and compared, so
   the doc cannot state a figure the evidence does not hold.

⛔ Nothing here runs the measurement — it needs a live FalkorDB and a real
capture corpus. That is deliberate: a test that needed a graph would be skipped
on CI and would guard nothing. What is pinned is the reasoning surface.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

DOC_DIR = (Path(__file__).resolve().parent.parent
           / "docs" / "research" / "2026-11-12-11901-candidate-recall")
BRIEF = DOC_DIR / "research-brief.md"
ARTIFACT = DOC_DIR / "measurement.json"              # hybrid — the product's read
CONTROL = DOC_DIR / "measurement-fts-only.json"      # the lexical-only control
TOOL = Path(__file__).resolve().parent.parent / "tools" / "candidate_recall_11901.py"


# ── the artifact ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def tool_mod():
    """The harness module, so a mode label is DERIVED rather than frozen."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "candidate_recall_11901", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def control() -> dict:
    assert CONTROL.exists(), f"missing committed control {CONTROL}"
    return json.loads(CONTROL.read_text())


def test_both_artifacts_declare_which_retrieval_mode_ran(report, control, tool_mod):
    """An FTS-only run is a DIFFERENT measurement from the fused read.

    `battery/runner/retrieval_preflight.py` records a keyword-only run published
    as a product measurement as the class this field exists to prevent, so the
    artifact must be unable to hide which mode produced it.
    """
    assert report["retrieval"]["has_embedder"] is True, (
        "the headline artifact is not hybrid — the brief's §3.1 table and the "
        "STORAGE-ARCHITECTURE sentence both rest on it being the fused read")
    assert report["retrieval"]["legs_seen"].get("vector") is True
    assert control["retrieval"]["has_embedder"] is False
    mod = tool_mod

    # ⛔ `--allow-mixed-modes` is a DOCUMENTED escape hatch that records a mixture
    # AS a mixture (the note then reads "MIXED: see window_modes"). The expected
    # note therefore has two arms: an artifact written through the hatch is not
    # "a note that contradicts its legs", and pinning only `_mode_note` here
    # reported a FALSE diagnosis of the hatch (review round 9, P2).
    def _expected_note(art: dict) -> str:
        r = art["retrieval"]
        if r.get("uniform_windows") is False:
            return "MIXED: see window_modes"
        return mod._mode_note(r["has_embedder"], r["legs_seen"])

    assert (control["retrieval"]["note"]
            == _expected_note(control)), (
        "the control's note is not the one its own legs imply")
    # ⛔ The hybrid artifact's note must be the one its OWN legs imply: a note
    # that contradicts `has_embedder`/`legs_seen` is the false label
    # `retrieval_preflight` exists to prevent, and asserting only that the
    # control mentions "KEYWORD-ONLY" leaves it unguarded (round 5, F2).
    # ⛔ DERIVED, not frozen: `_mode_note` is the invariant, so a legitimate
    # reword that regenerates the artifact is not read as a contradiction (the
    # first version pinned the literal "hybrid", which made a correct two-line
    # change fail with a FALSE diagnosis — review round 6, F3).
    note = report["retrieval"]
    assert note["note"] == _expected_note(report), (
        f"the hybrid artifact's note contradicts the legs it declares: "
        f"{note['note']!r} vs {_expected_note(report)!r}")
    for name, art in (("hybrid", report), ("control", control)):
        vector = bool(art["retrieval"]["legs_seen"].get("vector"))
        if art["retrieval"]["has_embedder"] and vector:
            assert "KEYWORD-ONLY" not in art["retrieval"]["note"], (
                f"{name}: declares a vector leg but its note says keyword-only")
    assert control["retrieval"]["legs_seen"].get("vector") is False


def test_the_dense_leg_moves_the_classes_in_opposite_directions(report, control):
    """The brief's §3.1 headline: the mode changes the answer, by class.

    If this stops holding, the sentence added to STORAGE-ARCHITECTURE.md is
    wrong and must be re-derived — it is quoted there as a measured finding.
    """
    for cls, expect in (("definite", "down"), ("ident", "up"), ("path", "up")):
        fts = control["arms"]["exact_by_class"][cls]["3"]
        hyb = report["arms"]["exact_by_class"][cls]["3"]
        assert fts["n"] == hyb["n"] > 0
        if expect == "down":
            assert hyb["in_window"] < fts["in_window"], f"{cls} did not get worse under hybrid"
        else:
            assert hyb["in_window"] > fts["in_window"], f"{cls} did not improve under hybrid"


def test_the_mode_note_is_derived_from_the_legs_not_from_availability():
    """A loaded model whose vector leg never ran must NOT be labelled hybrid.

    `retrieval_preflight` exists because a keyword-only run was published as a
    product measurement; deriving the label from `has_embedder` reproduces that
    class whenever the leg fails to answer (encode failure, breaker open, empty
    trace). The note must come from the trace.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "candidate_recall_11901",
        Path(__file__).resolve().parent.parent / "tools" / "candidate_recall_11901.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    hybrid = mod._mode_note(True, {"fts": True, "vector": True}).lower()
    assert "hybrid" in hybrid and "keyword-only" not in hybrid
    assert "KEYWORD-ONLY" in mod._mode_note(False, {"fts": True})
    misleading = mod._mode_note(True, {"fts": True, "vector": False})
    assert "DESPITE AN EMBEDDER" in misleading, (
        "an available embedder with no vector leg still reads as hybrid")
    assert misleading != "hybrid"


def test_the_pooled_set_is_measured_at_every_width(report):
    """§1/§7 quote a pooled figure at `limit=15`, not an extrapolation."""
    pooled = report["arms"]["pooled"]
    assert set(pooled) == {"3", "5", "10", "15"}
    means = [pooled[str(k)]["set_sum"] / pooled[str(k)]["captures"] for k in (3, 5, 10, 15)]
    assert means == sorted(means), "the pooled set must grow with the per-query width"
    # …and the budget it is measured at is stated, so "mean 40" is not read as
    # "the typical capture" (a capture with 5 entities issues 5 queries).
    assert "BUDGET CAP" in report["pooled_budget_note"]


def test_the_artifact_carries_every_arm_the_brief_quotes(report):
    arms = report["arms"]
    for key in ("exact", "exact_by_class", "exact_misses", "variant",
                "variant_by_class", "pooled"):
        assert key in arms, f"artifact lost arm {key!r} — the brief quotes it"
    assert report["issue"] == 11901
    # A measurement with no stated population is not a measurement.
    assert report["corpus"]["population"] > 0
    assert report["corpus"]["sample"] > 0
    # The harvest rules must stay the three declared classes; a fourth would be
    # an undeclared population change behind the same document.
    assert set(report["corpus"]["harvested_distinct"]) == {"definite", "ident", "path"}


def test_recall_is_monotone_in_k_and_misses_partition(report):
    """A wider window cannot lose a candidate, and the misses must add up.

    Both are properties of the measurement, not of the numbers: if either fails
    the artifact is internally inconsistent and no figure in it may be quoted.
    """
    for arm in ("exact", "variant"):
        prev = -1
        for k in sorted(report["arms"][arm], key=int):
            row = report["arms"][arm][k]
            got = row["in_window"]
            assert got >= prev, f"{arm} recall fell from {prev} to {got} at k={k}"
            prev = got
    n = report["corpus"]["sample"]
    for k, row in report["arms"]["exact"].items():
        missed = n - row["in_window"]
        assert row["miss_past_k"] + row["miss_absent_at_max_k"] == missed, (
            f"k={k}: the two miss states do not partition the {missed} misses")
        # "the leg returned nothing" is a sub-case of "absent at k=15", never a
        # third state — the distinction the brief's §3 table relies on.
        assert row["rows_zero"] <= row["miss_absent_at_max_k"]


@pytest.mark.parametrize("artifact", ["report", "control"])
def test_the_shipped_matcher_attaches_no_variant_at_any_width(request, artifact):
    """The load-bearing negative: width is not the lever for the variant class.

    ``_find_existing_entity`` is exact-``_norm`` equality, so a surface variant
    is unattachable at EVERY k. If this ever becomes non-zero, the brief's §4
    and §5 tables are wrong and the whole recommendation must be re-derived.
    """
    report = request.getfixturevalue(artifact)
    variant = report["arms"]["variant"]
    for k, row in variant.items():
        assert row["attached"] == 0, (
            f"k={k}: the shipped matcher attached {row['attached']} variants — "
            "the brief's §4 table and its recommendation are now stale")
        # …while a punctuation fold DOES bind the in-window separator class,
        # which is what makes it the measured next step rather than a guess.
        assert row["attached_folded"] > 0
        assert row["in_window_not_attached"] == row["in_window"]


def test_the_sample_allocation_sums_to_the_requested_size_and_starves_no_class():
    """The stratified shares must SUM to `--sample`, and no class may get zero.

    Independent rounding (`round(sample*n_c/N)`) plus a `[:sample]` truncation
    silently dropped the LAST class — measured: 124 of the sample sizes 1..600
    on the committed 600/300/100 mix, e.g. `--sample 6` → [4, 2, 1] = 7 → the
    path class disappeared while the artifact still said "sample: 6".
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "candidate_recall_11901", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pop = ([{"cls": "definite"}] * 600 + [{"cls": "ident"}] * 300
           + [{"cls": "path"}] * 100)
    # ⛔ The sizes below are NOT all multiples of the 600/300/100 mix. 270/300/450
    # divide exactly (162/81/27, 180/90/30, 270/135/45), so independent rounding
    # already sums correctly and the largest-remainder pass is never reached — a
    # review round DELETED that pass and this test stayed green (22 passed) while
    # `--sample 6` came out {definite: 3, ident: 1, path: 0}: sum 4, not 6. The
    # SUM is what the remainder pass guarantees, so the non-multiples discriminate.
    #
    # Non-starvation is a SEPARATE promise and it only binds once a class can have
    # a member at all: at `--sample 6` the proportional shares are 3.6 / 1.8 / 0.6
    # and an honest allocation gives `path: 0` (sum still 6) — `run()` REFUSES that
    # sample instead of sampling it (the "starves a class" guard, exit 2). So the
    # `>= 10` bound is where `path`'s 10 % share is worth a whole member, not a
    # weakened assertion.
    for sample in (6, 7, 19, 299, 270, 300, 450):
        shares = mod._allocate_shares(sample, pop)
        assert sum(shares.values()) == sample, (sample, shares)
        if sample >= 10:
            assert all(v > 0 for v in shares.values()), (sample, shares)
    # …and the interleave keeps every class in every full group of 15, which is
    # what makes the pooled arm a capture-shaped set rather than a class slice.
    shares = mod._allocate_shares(300, pop)
    by_cls = {"definite": [{"cls": "definite", "i": i} for i in range(180)],
              "ident": [{"cls": "ident", "i": i} for i in range(90)],
              "path": [{"cls": "path", "i": i} for i in range(30)]}
    ordered = mod._interleave(by_cls, shares)
    assert len(ordered) == 300
    # Measured shape: 14 of the 20 groups are class-mixed and 6 (the tail, once
    # the 120 minority names are exhausted) are definite-only — the honest shape
    # of a 60/30/10 population. What the interleave removes is the ALL-DEFINITE
    # head (12 of 20 groups were single-class before), not the arithmetic of the
    # mix. Pinned as an equality so a regression to class-blocking reds.
    mixed = sum(1 for start in range(0, 300, 15)
                if len({r["cls"] for r in ordered[start:start + 15]}) > 1)
    assert mixed == 14, f"{mixed} of 20 pooled groups are class-mixed, expected 14"


def test_search_graph_unions_the_derived_queries_into_one_id_keyed_dict(monkeypatch):
    """The mechanism behind "`limit=3` is not 3 candidates", pinned in the CODE.

    §1 consequence 1 and the "~40 vs 15" comparison rest on
    `extractor_v2.search_graph` accumulating EVERY derived query's rows into one
    id-keyed dict (`:3074`). The artifact's pooled arm re-implements that union,
    so an artifact-side test cannot see the production mechanism changing —
    §9 used to claim it was pinned when nothing read `search_graph` (round 6,
    F4). This drives the real function with a stub client.
    """
    from tortoise import extractor_v2 as v2

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setenv("TORTOISE_API_URL", "https://api.example.test")

    rows_by_query: dict[str, list[dict]] = {}
    queries_asked: list[str] = []

    class StubSDK:
        # no `_get_proj`: the env label decides searchability (the documented
        # mock path), and this pins the UNION, not the backend gate.
        def tortoise_fts_query(self, query, *, entity_type, limit=3,
                               include_terminal=False, **_ignored):
            queries_asked.append(f"{entity_type}:{query}")
            return rows_by_query.get(query, [])[:limit]

    # Two DERIVED queries return the SAME node plus a distinct one each, which
    # is what a widening-derived query set looks like in production.
    shared = {"id": "obj-shared", "content": "the retrieval step", "point_kind": "core:plan"}
    embed = {"entities": [{"name": "the retrieval step", "kind": "object"},
                          {"name": "the citation graph", "kind": "object"}]}
    res = v2.search_graph(StubSDK(), embed, "A story with a sentence.",
                          max_queries=15, limit=3)
    assert res["degraded"] is False, res.get("reason")
    asked = [q.split(":", 1)[1] for q in queries_asked if q.startswith("object:")]
    assert len(asked) >= 2, f"S3 ran fewer than two derived queries: {asked}"
    for i, q in enumerate(asked):
        # Two distinct rows per query, so a UNION must exceed `limit=3` — a
        # list capped at the per-query width would hold at most 3.
        rows_by_query[q] = [shared] + [
            {"id": f"obj-distinct-{i}-{j}", "content": f"{q} v{j}",
             "point_kind": "core:plan"} for j in (0, 1)]
    # Re-drive now that every query answers, so the union is exercised.
    res = v2.search_graph(StubSDK(), embed, "A story with a sentence.",
                          max_queries=15, limit=3)
    ids = [e["id"] for e in res["entities"]]
    assert ids.count("obj-shared") == 1, (
        f"the shared row was not deduplicated by id: {ids}")
    assert len(ids) == len(set(ids)), f"duplicate ids survived the union: {ids}"
    assert len(ids) > 3, (
        "the candidate list is not the UNION of the derived queries — it looks "
        f"capped at `limit`: {ids}")


def test_the_pooled_set_is_wider_than_the_per_query_window(report):
    """``limit`` is not the candidate count — `search_graph` unions the queries.

    The brief's §1 consequence #1 and its "Zep's 15 is a narrowing for us"
    correction both rest on this. A pooled set at or below ``limit`` would mean
    the union is not happening.
    """
    pooled = report["arms"]["pooled"]["3"]
    assert pooled["captures"] > 0
    mean = pooled["set_sum"] / pooled["captures"]
    assert pooled["set_min"] > 3, (
        f"pooled minimum {pooled['set_min']} is not above the per-query limit=3")
    assert mean > pooled["set_min"]


# ── the code the brief's mechanism claims are about ──────────────────────────

def test_derive_queries_emits_one_object_query_per_entity_name():
    from tortoise.extractor_v2 import _derive_queries

    embed = {"entities": [{"name": "the plan doc"}, {"name": "the timeout command"},
                          {"name": "the plan doc"}],  # duplicate collapses
             "events": [], "points": []}
    queries = _derive_queries(embed, "")
    assert queries["object"] == ["the plan doc", "the timeout command"]


def test_the_matcher_is_norm_exact_AND_gains_the_folded_rung_12295():
    """§4/§5's premise, RESTATED — and the pin that moved is named (#12295).

    ``_norm`` is whitespace-collapse + lowercase and nothing else. That stays
    true, and it must: ``_norm``'s other consumers assume exactly this narrow
    contract, which is why the fold belongs in the COMPARISON and not in
    ``_norm``.

    What changed: phase 1 now tries the comparison-side fold (`#12295`,
    ``entity_blocking.attach_matches``) after the exact arms miss, so a surface
    variant IS visible to phase 1. ⚠️ The committed artifacts
    (``measurement.json`` / ``measurement-fts-only.json``) were taken BEFORE
    this rung landed, so their ``attached: 0`` arm describes the pre-#12295
    matcher — the ``docs/research/2026-11-12-12295-*`` measurement is the
    before/after that supersedes it. This test pins both halves so neither can
    drift silently in the "0 %" direction (the arm the brief's headline rests on)
    or in the "it always worked" direction.
    """
    from tortoise.extractor_v2 import _find_existing_entity, _norm

    assert _norm("The  Plan-Doc") == "the plan-doc"
    existing = [{"id": "obj-1", "name": "the plan doc", "kind": "core:other"}]
    assert _find_existing_entity(existing, "the plan doc", "core:other")[1] == "exact"

    # The old arms are byte-identical, so the pre-#12295 number is reproducible:
    # `_norm` still folds nothing here, i.e. the EXACT arm still misses.
    for variant in ("plan doc", "the-plan-doc", "the plan-doc"):
        assert _norm(variant) != _norm("the plan doc"), (
            f"{variant!r} is now folded by _norm — the fold escaped the comparison")
        ex, mode = _find_existing_entity(existing, variant, "core:other")
        assert mode not in ("exact", "bare"), (
            f"{variant!r} bound through the pre-#12295 arm ({mode}) — the folded "
            "rung's contribution would be hidden")
    # …and the #12295 folded rung binds them, on the SAME node (id, not name):
    for variant in ("plan doc", "the plan-doc"):
        ex, mode = _find_existing_entity(existing, variant, "core:other")
        assert ex is not None and ex["id"] == "obj-1", (
            f"{variant!r} did not resolve via the folded rung — #11901's 0 %/419 "
            "arm is only beaten if this binds")
        assert mode == "folded"

    # ⛔ THE RESIDUAL, pinned so it cannot be mistaken for coverage.
    # ``the-plan-doc`` keeps its article: ``normalize_entity_name`` anchors the
    # article strip on a real whitespace boundary BEFORE folding punctuation
    # (#12047 round-4 finding 5 — the fix that stopped ``a-b`` folding to
    # ``b``). So an article + separator variant of a ``the …`` name does NOT
    # bind, and the measurement must say so rather than imply the whole
    # separator class moved.
    ex, mode = _find_existing_entity(existing, "the-plan-doc", "core:other")
    assert ex is None and mode == "none", (
        "the-plan-doc now binds — if that is a deliberate new comparison key it "
        "must arrive with its own precision pin for 'a-b' vs 'b' (#12047 R4F5)")


def test_the_article_anchor_decision_is_not_reversed():
    """#12047 round-4 finding 5, pinned from the outside (#12295 touches the
    comparison and must not re-open the fold): `a-b` is NOT `b`."""
    from tortoise.extractor_v2 import _find_existing_entity

    existing = [{"id": "obj-b", "name": "b", "kind": "core:other"}]
    ex, mode = _find_existing_entity(existing, "a-b", "core:other")
    assert ex is None and mode == "none", f"'a-b' folded onto 'b' as {mode}"


def test_a_different_noun_never_binds_through_the_folded_rung():
    """The fold must not become a fuzzy matcher (#2730 §6.7: a false merge is
    invisible and hard to undo). No similarity, no threshold — so a near-miss
    stays a near-miss."""
    from tortoise.extractor_v2 import _find_existing_entity

    existing = [{"id": "obj-1", "name": "the plan doc", "kind": "core:other"}]
    for other in ("the play doc", "plan", "the plan docs", "the doc"):
        ex, mode = _find_existing_entity(existing, other, "core:other")
        assert ex is None and mode == "none", f"{other!r} bound as {mode} — too fuzzy"


def test_the_folded_rung_refuses_an_ambiguous_collision():
    """Two live carriers of one fold is legal since #3590 — never guess.

    §4.4 I1: an unresolved identity is NEVER asserted `new`, so the caller holds
    it. Returning either id would be a silent pick (`entity_identity`'s own
    route-then-refuse rule).
    """
    from tortoise.extractor_v2 import _find_existing_entity

    existing = [{"id": "obj-1", "name": "the plan doc", "kind": "core:other"},
                {"id": "obj-2", "name": "plan-doc", "kind": "core:other"}]
    ex, mode = _find_existing_entity(existing, "plan_doc", "core:other")
    assert ex is None and mode == "ambiguous"


def test_the_variant_classes_are_the_ones_norm_does_not_fold():
    """The harness's variant set must stay the normalisation gap it claims.

    A transformation that ``_norm`` already folds would measure nothing and
    would inflate the 0% result.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "candidate_recall_11901",
        Path(__file__).resolve().parent.parent / "tools" / "candidate_recall_11901.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from tortoise.extractor_v2 import _norm

    cases = {"the admin-merge rail": "definite", "the plan doc": "definite",
             "tortoise/hosted_api.py": "path", "#11901": "ident"}
    for name, cls in cases.items():
        made = mod.variants(name, cls)
        assert made, f"{name!r} produced no variant"
        for vclass, surface in made:
            assert _norm(surface) != _norm(name), (
                f"{vclass} of {name!r} is already folded by _norm — it measures nothing")
    # …and the fold the brief proposes does NOT merge a genuinely different name.
    assert mod._fold_punct("the admin-merge rail") == mod._fold_punct("the admin merge rail")
    # …and it folds the separator class: the two spellings agree after it.
    assert mod._fold_punct("the plan doc") == mod._fold_punct("the plan-doc ")


def test_the_brief_quotes_the_crowding_statistic_the_artifact_holds(report):
    """§3's "mean 4.0 … median 2" is DERIVED — recompute it, do not trust prose.

    This assertion exists because the first draft of the brief quoted a figure
    from an earlier, differently-sized run ("53 … mean 8.5, median 5") that the
    committed artifact does not hold. A derived statistic is as quotable as a
    stored one and rots the same way.
    """
    import statistics

    misses = report["arms"]["exact_misses"]
    assert misses, "the artifact records no misses — §3's crowding sentence is unsupported"
    mean = statistics.mean(m["siblings_2tok"] for m in misses)
    median = statistics.median(m["siblings_2tok"] for m in misses)
    text = _squash(BRIEF.read_text())
    assert f"mean of {mean:.1f} population names with ≥2 tokens in common" in text, (
        f"the brief does not state the recomputed crowding figure "
        f"(mean {mean:.1f}, median {median:g})")


# ── the brief's tables must equal the artifact ──────────────────────────────

def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", line):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_the_brief_quotes_the_recall_the_artifact_holds(report, control):
    """§3's two tables, ROW BY ROW, against BOTH artifacts — every column.

    ⛔ Position-blind substring checks are not a guard: a row carrying ANOTHER
    row's number still matches, and a per-cell check with no k label lets a k=15
    value be pasted into the k=3 cell. Each assertion below is a whole squashed
    row — k, recall, past-this-k, absent-at-15 and rows_zero together — and the
    control's table is checked too (round 2, F4; round 3, F4).
    """
    text = _squash(BRIEF.read_text())
    # ⛔ ANCHOR each artifact's rows to ITS OWN section. Asserting both whole
    # rows somewhere in the brief is position-blind: with the two §3 table BODIES
    # swapped (so the headline "HYBRID — the product's read" table published the
    # keyword-only numbers, the exact false-product-measurement class
    # `retrieval_preflight` exists to prevent) both `want` strings are still
    # present and this test passed — measured, review round 8. §3.1's row is
    # position-anchored because both columns live in one row; §3's was not.
    head, sep, rest = text.partition("KEYWORD-ONLY — the lexical control")
    assert sep, "§3's control table heading is gone — cannot anchor its rows"
    assert "HYBRID — the product's read" in head, "§3's hybrid table heading is gone"
    for art, label, body in ((report, "hybrid", head),
                             (control, "keyword-only", rest)):
        n = art["corpus"]["sample"]
        for k in ("3", "5", "10", "15"):
            row = art["arms"]["exact"][k]
            pct = 100 * row["in_window"] / n
            want = (f"| {k} | {row['in_window']}/{n} = {pct:.1f} % | "
                    f"{row['miss_past_k']} | {row['miss_absent_at_max_k']} | "
                    f"{row['rows_zero']} |")
            assert want in body, (
                f"§3 {label} k={k}: the brief does not carry `{want}` in that "
                f"artifact's OWN section")
    # §3's prose figures follow BOTH tables, so they are checked against the whole
    # document; they are recomputed from the HYBRID k=3 row and are unique.
    h3 = report["arms"]["exact"]["3"]
    absent = h3["miss_absent_at_max_k"]
    past = h3["miss_past_k"]
    total = past + absent
    assert f"{past} of {total} misses ({100 * past / total:.1f} %)" in text, (
        f"§3 does not state that the width fixes {past} of {total} misses")
    assert f"{absent} misses ({100 * absent / report['corpus']['sample']:.1f} % of the sample)" in text, (
        f"§3 does not state the {absent} misses absent even at k=15")


def test_the_brief_quotes_the_class_rates_and_the_mean_rank(report, control):
    """§3's mean-rank figure and §3.1's nine class cells — recomputed, not trusted.

    Both are DERIVED. The first draft of the brief quoted the k=5 mean rank as
    the k=3 one and a class section the artifact did not hold; a guard that only
    checks a direction cannot catch either.
    """
    text = _squash(BRIEF.read_text())
    hyb3 = report["arms"]["exact"]["3"]
    mean_rank = hyb3["rank_sum"] / hyb3["in_window"]
    assert f"{mean_rank:.1f} at k=3" in text, (
        f"the brief does not state the k=3 mean rank ({mean_rank:.1f})")
    labels = {"definite": "definite description",
              "ident": "identifier (`#n`, `snake_case`)",
              "path": "path"}
    for cls, label in labels.items():
        f = control["arms"]["exact_by_class"][cls]["3"]
        h = report["arms"]["exact_by_class"][cls]["3"]
        h15 = report["arms"]["exact_by_class"][cls]["15"]
        pf, ph, p15 = (100 * f["in_window"] / f["n"],
                       100 * h["in_window"] / h["n"],
                       100 * h15["in_window"] / h15["n"])
        delta = ph - pf
        sign = "\u2212" if delta < 0 else "+"
        # The WHOLE row, including the derived delta and the k=15 column:
        # a k=3 cell that happens to equal its own k=15 cell is otherwise
        # indistinguishable from a correctly-filled one.
        want = (f"| {label} | {h['n']} | {f['in_window']} = {pf:.1f} % | "
                f"{h['in_window']} = {ph:.1f} % | {sign}{abs(delta):.1f} pts | "
                f"{h15['in_window']} = {_pct_cell(p15)} % |")
        assert want in text, f"§3.1 {cls}: the brief does not carry `{want}`"


def test_the_brief_quotes_the_variant_rates_the_artifact_holds(report, control):
    """§4's two tables — the hybrid arm row by row, and the control's prose.

    Round 3 deleted the only guard over `arms.variant[*]["in_window"]`, so a
    re-derived artifact could swap the control's in-window column into the
    hybrid table and 17/17 would still pass while §4/§5/§7 and the PR body
    asserted the old figure.
    """
    text = _squash(BRIEF.read_text())
    pairs = report["arms"]["variant"]["3"]["pairs"]
    for k in ("3", "5", "10", "15"):
        v = report["arms"]["variant"][k]
        want = (f"| {k} | {pairs} | {v['in_window']} = "
                f"{100 * v['in_window'] / pairs:.1f} % | "
                f"{v['attached']} = {100 * v['attached'] / pairs:.1f} % | "
                f"{v['attached_folded']} = {100 * v['attached_folded'] / pairs:.1f} % |")
        assert want in text, f"§4 hybrid k={k}: the brief does not carry `{want}`"
        assert v["pairs"] == pairs
    c3, c15 = control["arms"]["variant"]["3"], control["arms"]["variant"]["15"]
    c_pairs = c3["pairs"]
    assert (f"in-window {c3['in_window']} ({100 * c3['in_window'] / c_pairs:.1f} %) → "
            f"{c15['in_window']} ({100 * c15['in_window'] / c_pairs:.1f} %)") in text, (
        "§4's control sentence does not state the control's own in-window rates")
    # …and the SAME sentence's fold column: it is the figure §7-2b's
    # mode-independence argument cites beside the hybrid one, and a loose
    # `attached_folded > 0` does not pin it (round 4, F1).
    assert (f"folded {c3['attached_folded']} ({100 * c3['attached_folded'] / c_pairs:.1f} %) → "
            f"{c15['attached_folded']} ({100 * c15['attached_folded'] / c_pairs:.1f} %)") in text, (
        "§4's control sentence does not state the control's own fold column")
    # The brief aggregates the two separator directions into one row; the
    # artifact keeps them apart because the fold only helps one of them
    # (`space→hyphen` is 101/180 in window, `hyphen→space` is 13/13).
    sep3 = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "3")
    sep15 = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "15")
    labels = {"article-drop": "article dropped (`the plan doc` → `plan doc`)",
              "basename-only": "path basename only",
              "hash-dropped": "`#` dropped (`#11901` → `11901`)"}
    rows = [(labels[c], report["arms"]["variant_by_class"][c]["3"],
             report["arms"]["variant_by_class"][c]["15"]) for c in labels]
    rows.append(("separator (`the admin-merge rail` ⇄ `the admin merge rail`)",
                 sep3, sep15))
    for label, b3, b15 in rows:
        want = (f"| {label} | {b3['pairs']} | {b3['in_window']} → "
                f"{b15['in_window']} | {b3['attached']} → {b15['attached']} | "
                f"{b3['attached_folded']} → {b15['attached_folded']} |")
        assert want in text, f"§4 decomposition {label!r}: the brief does not carry `{want}`"


def _pct_cell(pct: float) -> str:
    """"100" not "100.0" — the brief writes a whole percentage without a decimal."""
    return f"{pct:.1f}".replace(".0", "") if abs(pct - round(pct)) < 0.05 else f"{pct:.1f}"


def _sum_cls(report, classes, k):
    """Sum a set of variant classes at one width, for a row the brief aggregates."""
    out = {}
    for cls in classes:
        row = report["arms"]["variant_by_class"][cls][k]
        for key, val in row.items():
            out[key] = out.get(key, 0) + val
    return out


def _squash(text: str) -> str:
    """Formatting-insensitive view: a line wrap or bold marker is not a figure.

    Strips the markdown emphasis a table cell uses, so the guard tests WHICH
    NUMBER the brief states and not how it was typeset.
    """
    return re.sub(r"\s+", " ", text.replace("*", ""))


def test_the_brief_quotes_the_pooled_set_the_artifact_holds(report):
    """§1's pooled table, every width — the union is the real candidate list.

    Round 3 caught the PR body quoting the CONTROL's k=5 mean (43.9) as a
    hybrid measurement, which no test could see because only k=3 was pinned.
    The table now carries all four widths and each row is pinned with its k.
    """
    text = _squash(BRIEF.read_text())
    for k in ("3", "5", "10", "15"):
        pooled = report["arms"]["pooled"][k]
        mean = pooled["set_sum"] / pooled["captures"]
        assert (f"| {k} | {mean:.1f} | {pooled['set_min']} | {pooled['set_max']} |") in text, (
            f"§1 pooled k={k}: the brief does not carry "
            f"`| {k} | {mean:.1f} | {pooled['set_min']} | {pooled['set_max']} |`")


def test_the_briefs_recommendation_repeats_only_recomputed_figures(report, control):
    """§7 restates §3 and §3.1's figures; they are recomputed HERE too.

    The recommendation section is where a follow-up lane reads the numbers it is
    told to act on, and it was outside every guard — so §7 could say "paths by
    63.4" (the exact figure round 3 corrected) with a green suite (round 4, F4).
    """
    text = _squash(BRIEF.read_text())
    h, f = report["arms"]["exact_by_class"], control["arms"]["exact_by_class"]
    d_def = 100 * (h["definite"]["3"]["in_window"] / h["definite"]["3"]["n"]
                   - f["definite"]["3"]["in_window"] / f["definite"]["3"]["n"])
    d_id = 100 * (h["ident"]["3"]["in_window"] / h["ident"]["3"]["n"]
                  - f["ident"]["3"]["in_window"] / f["ident"]["3"]["n"])
    d_path = 100 * (h["path"]["3"]["in_window"] / h["path"]["3"]["n"]
                    - f["path"]["3"]["in_window"] / f["path"]["3"]["n"])
    assert f"by {abs(d_id):.1f} and paths by {abs(d_path):.1f}" in text, (
        f"§7 does not state the recomputed class deltas "
        f"({abs(d_id):.1f} / {abs(d_path):.1f})")
    assert f"class by {abs(d_def):.1f} points" in text, (
        f"§7 does not state the recomputed definite-description delta ({abs(d_def):.1f})")
    exact3 = report["arms"]["exact"]["3"]
    total = exact3["miss_past_k"] + exact3["miss_absent_at_max_k"]
    assert f"It recovers {100 * exact3['miss_past_k'] / total:.1f} %" in text, (
        "§7 does not state the recomputed share of misses a wider window fixes")
    # §7's zero-row sentence (control): the count, its sample share, and the
    # identifier share of it — all three recomputed.
    cz = control["arms"]["exact"]["3"]["rows_zero"]
    idz = control["arms"]["exact_by_class"]["ident"]["3"]["rows_zero"]
    assert f"zero rows for {cz}/{control['corpus']['sample']} names" in text, (
        f"§7 does not state the control's zero-row count ({cz})")
    assert f"{idz} of them identifiers" in text, (
        f"§7 does not state the identifier share of the zero-row class ({idz})")
    assert report["arms"]["exact"]["3"]["rows_zero"] == 0, (
        "the hybrid arm's zero-row claim (`rows_zero` 0) is no longer what the artifact holds")


def test_the_briefs_restatements_all_recompute(report, control):
    """Every figure the brief repeats OUTSIDE its main tables — §5, §7, §8, §6, §3.

    Round 5's point was that pinning the strings a round names closes instances,
    not the class: §5 is a table and matched nothing, and eight §7/§8/§6/§3
    restatements could be edited with a green suite. Each literal below is built
    from the artifact, so the guard is over the SURFACE, not over a list of
    findings.
    """
    import collections
    import statistics

    text = _squash(BRIEF.read_text())
    v = report["arms"]["variant"]
    pairs = v["3"]["pairs"]

    def pct(num, den):
        return f"{100 * num / den:.1f} %"

    # ── §5: its definitional first row, then the four measured rows.
    assert "| prior off (no link-before-create) | 0 % | n/a |" in text, (
        "§5's definitional `prior off` row changed — it states the baseline the "
        "other four rows are read against")

    for k in ("3", "15"):
        row = v[k]
        assert (f"| prior on, `limit={k}`, phase 1 | 0.0 % | "
                f"{pct(row['in_window'], pairs)} |") in text, (
            f"§5's `limit={k}` row does not match the artifact")
        assert (f"| prior on, `limit={k}`, phase 1 + punctuation fold (NOT shipped) | "
                f"{pct(row['attached_folded'], pairs)} | {pct(row['in_window'], pairs)} |") in text, (
            f"§5's folded `limit={k}` row does not match the artifact")

    # ── §3: the identifier share of the k=3 misses, and the crowding median.
    misses = report["arms"]["exact_misses"]
    by_cls = collections.Counter(m["cls"] for m in misses)
    assert f"{by_cls['ident']} of the {len(misses)} misses" in text, (
        f"§3 does not state the identifier share of the misses ({by_cls['ident']})")
    median = statistics.median(m["siblings_2tok"] for m in misses)
    assert f"(median {median:g})" in text, (
        f"§3 does not state the recomputed crowding median ({median:g})")

    # ── §3: the keyword-only miss split and the k=5 mean rank. Both DERIVED
    # (28 + 106 = 134; rank_sum / in_window), and both were unpinned until a
    # review round mutated them with a green suite — the "derived figure, not the
    # stored one" class the k=3 mean-rank guard above already closed for ONE of
    # the two ranks.
    ck3 = control["arms"]["exact"]["3"]
    kw_past, kw_absent = ck3["miss_past_k"], ck3["miss_absent_at_max_k"]
    assert (f"Keyword-only: {kw_past} of {kw_past + kw_absent}, "
            f"with {kw_absent} absent at 15") in text, (
        "§3 does not state the recomputed keyword-only miss split "
        f"({kw_past} + {kw_absent})")
    h5 = report["arms"]["exact"]["5"]
    mean_rank5 = h5["rank_sum"] / h5["in_window"]
    assert f"{mean_rank5:.1f} at k=5" in text, (
        f"§3 does not state the k=5 mean rank ({mean_rank5:.1f})")

    # ── §2: the between-run drift. `corpus.harvested_distinct` IS in the
    # artifact, so this pair and the percentage derived from it are pinnable —
    # they were simply never pinned.
    d_hyb = report["corpus"]["harvested_distinct"]["definite"]
    d_ctl = control["corpus"]["harvested_distinct"]["definite"]
    drift = 100 * (d_ctl - d_hyb) / d_hyb
    assert f"{d_hyb:,} (hybrid) and {d_ctl:,} (control)" in text, (
        f"§2 does not state the harvested_distinct pair ({d_hyb:,} / {d_ctl:,})")
    assert f"a {drift:.2f} % drift" in text, (
        f"§2 does not state the recomputed between-run drift ({drift:.2f} %)")

    # ── §2: the corpus SHAPE the artifacts record. Both are committed
    # integers — the file count and the population size — so they are pinnable
    # in one line each, and §9 has to be right about which corpus facts are
    # pinned and which are the live-collapse material it cannot pin.
    assert f"{report['corpus']['files']} daily files" in text, (
        f"§2 does not state the artifact's file count ({report['corpus']['files']})")
    assert (f"Population {report['corpus']['population']:,}, "
            f"stratified sample {report['corpus']['sample']}.") in text, (
        "§2 does not state the artifact's population and sample sizes "
        f"({report['corpus']['population']:,} / {report['corpus']['sample']})")

    # ── §1's table caption: the pooled-arm DENOMINATOR. Round 4 found `20`
    # unpinned, which is the population the whole "~40 vs 15" argument rests on.
    caps = report["arms"]["pooled"]["3"]["captures"]
    assert f"hybrid, {caps} captures" in text, (
        f"§1's caption does not state the pooled denominator ({caps} captures)")

    # ── §2's DRIFT INTERVAL, derived from each artifact's own clock: each run
    # harvests at t0 and records `measured_at` at the END, so the harvest time is
    # `measured_at − corpus.elapsed_s`. Round 5 found the fixed 427 s and 390 s
    # unguarded — computable, therefore pinnable.
    from datetime import datetime, timedelta

    def _harvest_at(art):
        return (datetime.strptime(art["measured_at"], "%Y-%m-%dT%H:%M:%SZ")
                - timedelta(seconds=art["corpus"]["elapsed_s"]))

    # `round()` returns an int for a float argument, so `int(...)` around it was
    # a no-op cast (ruff RUF046).
    gap_s = round((_harvest_at(control) - _harvest_at(report)).total_seconds())
    assert f"the two harvests {gap_s} s apart" in text, (
        f"§2 does not state the recomputed harvest-to-harvest interval ({gap_s} s)")
    assert f"the hybrid sweep alone took {report['corpus']['elapsed_s']:.0f} s" in text, (
        "§2 does not state the hybrid sweep's own wall clock, which is why the "
        "interval cannot be the report gap")

    # ── §3's miss COUNT, which §3.1's crowding statistic is computed over.
    assert f"The {len(misses)} recorded hybrid k=3 misses" in text, (
        "§3 does not state the number of misses its crowding mean is taken over "
        f"({len(misses)})")

    # ── §4's prose pair: the in-window rise, and the fold's 100 % of in-window.
    # Both were unpinned (round 4): the pair is DERIVED from `variant`, and the
    # 100 % is a ratio of two sums, so neither was visible to a row guard.
    v3, v15 = report["arms"]["variant"]["3"], report["arms"]["variant"]["15"]
    pairs_v = v3["pairs"]
    assert (f"({100 * v3['in_window'] / pairs_v:.1f} % → "
            f"{100 * v15['in_window'] / pairs_v:.1f} %)") in text, (
        "§4 does not state the recomputed in-window rise across the widths")
    sep3 = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "3")
    assert (f"{_pct_cell(100 * sep3['attached_folded'] / sep3['in_window'])} % "
            f"of the in-window") in text, (
        "§4's 'recovers X % of the in-window separator variants' does not match "
        f"the artifact ({sep3['attached_folded']}/{sep3['in_window']})")

    # ── §6: the headline restated in the external-findings section.
    exact = report["arms"]["exact"]
    n = report["corpus"]["sample"]
    assert f"Our {pct(exact['3']['in_window'], n)} hybrid recall@3" in text, (
        "§6 does not restate the recomputed hybrid recall@3")

    # ── §7-1: the width's reach, both ends.
    assert (f"({pct(exact['3']['in_window'], n)} → "
            f"{pct(exact['15']['in_window'], n)})") in text, (
        "§7-1 does not state the recomputed k=3 → k=15 recall")
    assert f"measured {report['arms']['pooled']['15']['set_sum'] / report['arms']['pooled']['15']['captures']:.1f}" in text, (
        "§7-1 does not state the recomputed pooled k=15 mean")

    # ── §7-1: the width's effect on PHASE 1 — the sentence the whole
    # "widening is not the lever" argument rests on (round 7, F1).
    assert f"attach rate by {pct(v['3']['attached'], pairs)}" in text, (
        "§7-1 does not state the recomputed phase-1 attach rate")
    # ── §7-2b: the fold's reach over IN-WINDOW separator variants (a different
    # denominator from the arm above) and §7-2d's article-class share.
    sep3_share = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "3")
    # BOTH legs from the artifact: a literal `_pct_cell(0)` let `90 % → 100 %`
    # pass, because a substring check cannot tell 0 from 90 (round 8, F1).
    # ⛔ The leading space is the boundary: "0 % → 100 %" is a SUBSTRING of
    # "90 % → 100 %", so without it the from-leg is still unguarded.
    assert (f" {_pct_cell(100 * sep3_share['attached'] / sep3_share['in_window'])} % → "
            f"{_pct_cell(100 * sep3_share['attached_folded'] / sep3_share['in_window'])} % "
            f"of in-window separator variants") in text, (
        "§7-2b does not state the recomputed in-window separator recovery")
    # "~40 vs 15" is restated three times (§1, §6, §7-1) and every one must be
    # the artifact's rounded mean: a single substring check over the FIRST
    # occurrence left the other two free to drift (round 8, F2).
    arm3 = report["arms"]["pooled"]["3"]
    rounded = f"~{arm3['set_sum'] / arm3['captures']:.0f}"
    assert text.count(rounded) >= 3, (
        f"the brief no longer restates {rounded!r} in all three places "
        f"(found {text.count(rounded)}); one has drifted from the artifact's "
        f"{arm3['set_sum'] / arm3['captures']:.1f}")
    article = report["arms"]["variant_by_class"]["article-drop"]["3"]["pairs"]
    assert f"({article}/{pairs})" in text, (
        f"§7-2d does not state the article class's share of the {pairs} pairs")

    # ── §7-2a: how many variants phase 2 is handed.
    assert (f"handed {pct(v['3']['in_window'], pairs)} of those variants") in text, (
        "§7-2a does not state the recomputed share phase 2 receives")

    # ── §7-2b: the fold's two figures and the width's residual gain.
    fold3 = v["3"]["attached_folded"]
    assert f"0 % → {pct(fold3, pairs)}" in text, (
        f"§7-2b does not state the recomputed 0 → {pct(fold3, pairs)}")
    # "100 % of in-window separator variants" is 114/114 and 117/117 — the
    # fold recovers every IN-WINDOW separator pair, which is a different
    # denominator from the 419-pair arm above.
    sep3 = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "3")
    sep15 = _sum_cls(report, ("hyphen-to-space", "space-to-hyphen"), "15")
    assert sep3["attached_folded"] == sep3["in_window"], (
        "the fold no longer recovers every in-window separator variant at k=3")
    assert sep15["attached_folded"] == sep15["in_window"], (
        "the fold no longer recovers every in-window separator variant at k=15")
    assert (f"({sep3['in_window']}/{sep3['in_window']} at k=3, "
            f"{sep15['in_window']}/{sep15['in_window']} at k=15)") in text, (
        "§7-2b does not state the recomputed in-window separator fractions")
    gain = 100 * (v["15"]["attached_folded"] - v["3"]["attached_folded"]) / pairs
    assert f"+{gain:.1f} pts" in text, (
        f"§7-2b does not state the recomputed width residual (+{gain:.1f} pts)")

    # ── §7-3: BOTH clocks — the hybrid sweep's and the control's.
    assert f"completed in {report['corpus']['elapsed_s']:.0f} s" in text, (
        "§7-3 does not state the hybrid artifact's own elapsed time")
    assert f"the keyword-only control took {control['corpus']['elapsed_s']:.0f} s" in text, (
        "§7-3 does not state the control artifact's own elapsed time")

    # ── §8: the hybrid zero-row claim is the artifact's, not prose.
    assert f"(`rows_zero` {report['arms']['exact']['3']['rows_zero']} in hybrid)" in text, (
        "§8's `rows_zero` claim does not match the hybrid artifact")


def test_the_brief_keeps_its_two_standing_negatives():
    """Promises a future lane must not quietly break while editing the doc."""
    text = BRIEF.read_text()
    # The LongMemEval run report stays PENDING; this doc must never claim a figure.
    assert "PENDING" in text and "no LongMemEval figure of our own is claimed" in text
    # The KEEP ruling is quoted, never re-opened.
    assert "No pass may remove those embeddings" in text
    assert "reopen" in text


# ── the two REFUSALS ─────────────────────────────────────────────────────────
# Both guards are the deliverable's load-bearing safety property, and a review
# round proved NEITHER was pinned: deleting the population-match refusal
# (`stale = []`) and replacing the per-window `require_hybrid_read` verdict with
# the loose `legs_seen` predicate both left the suite green (22 passed) — i.e. a
# regression to exactly the #8919 graph-reuse class or the #3005/#2952
# false-label class would have shipped under a suite that claims to pin them.
# These two tests drive `main()` so they fail if the refusal, the exit code, or
# the no-artifact-on-refusal property is removed.


class _FakeQuery:
    def __init__(self, rows):
        self.result_set = rows


class _FakeProj:
    """The projection surface `run()` uses: `_upsert_object` and `g.query`."""

    def __init__(self, stored_names):
        self._stored = stored_names
        self.g = self

    def query(self, *_a, **_k):
        return _FakeQuery([(f"obj-11901{i:06d}", n)
                           for i, n in enumerate(self._stored)])

    def _upsert_object(self, _row):
        return None


def _drive(tool_mod, monkeypatch, tmp_path, *, stored_names, trace_maker, argv=None,
           pop=None, rows_maker=None):
    """Run `main()` against a fake SDK; return (exit code, out path).

    ``rows_maker`` is optional and defaults to the refusing tests' behaviour (no
    rows at all). Pass it to drive the SUCCESS path: the arms' accounting only
    exercises its found-the-canonical branches when the leg returns a row, and
    `tests/test_name_entropy_12294.py` pins the predicate, not the accounting.
    """
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "docker://:falkordb@localhost:6379/recall_unit")
    if pop is None:
        pop = [{"cls": "definite", "name": "the alpha"},
               {"cls": "ident", "name": "AB-12"},
               {"cls": "path", "name": "src/beta.py"}]
    def _fake_harvest(_d, stats=None):
        # `run()` passes `stats=` to the REAL harvest (the peer lane's input
        # accounting), so the fake must accept it — an integration surface a
        # merge can silently break, and did: the fake's arity was fixed while
        # the production signature grew.
        if stats is not None:
            stats.update(lines=3, parse_errors=0, records_without_text=0)
        return {"definite": {"the alpha": 1},
                "ident": {"AB-12": 1},
                "path": {"src/beta.py": 1}}

    monkeypatch.setattr(tool_mod, "harvest", _fake_harvest)
    monkeypatch.setattr(tool_mod, "build_population", lambda _h, _n: pop)

    class _FakeSDK:
        def __init__(self, **_kw):
            self._proj = _FakeProj(stored_names)

        def _get_proj(self):
            return self._proj

        def tortoise_fts_query(self, q, **kw):
            trace = kw.get("leg_trace")
            if trace is not None:
                trace.append(trace_maker())
            return rows_maker(q) if rows_maker is not None else []

        def close(self):
            return None

    monkeypatch.setattr("tortoise.sdk.TortoiseSDK", _FakeSDK)
    out = tmp_path / "artifact.json"
    rc = tool_mod.main(argv or ["--population", "3", "--sample", "3",
                                "--captures", "2", "--out", str(out)])
    return rc, out


def test_a_reused_graph_is_refused_and_no_artifact_is_written(tool_mod, monkeypatch,
                                                             tmp_path):
    """`o.name` is an ON CREATE write (#8919): a stale name UNDERSTATES recall.

    The graph is not re-provable after the fact, so the run must refuse rather
    than measure a population that did not land. Mutation that must red this:
    `stale = []` in `run()`.
    """
    rc, out = _drive(
        tool_mod, monkeypatch, tmp_path,
        # the ids are the ones `run()` mints, but the NAMES are a previous run's
        stored_names=["the BETA", "CD-99", "src/gamma.py"],
        trace_maker=lambda: {"leg": "vector", "ran": True, "degraded": False})
    assert rc == 3, f"a population that did not land was not refused (rc={rc})"
    assert not out.exists(), "an artifact was written from a population that did not land"


def test_a_mixed_mode_run_is_refused_and_no_artifact_is_written(tool_mod, monkeypatch,
                                                               tmp_path):
    """A mixture published as one mode is the #2952/#3005 false label.

    The trace alternates healthy and breaker-open vector legs, so `legs_seen`
    (OR-ed over the sweep) says `hybrid` for the whole run while the STRICT
    per-window verdict is a mixture. Mutation that must red this: replacing
    `require_hybrid_read(trace)` with the `legs_seen` predicate in `run()`.
    """
    state = {"n": 0}

    def _trace():
        state["n"] += 1
        if state["n"] % 2:
            return {"leg": "vector", "ran": True, "degraded": False}
        return {"leg": "vector", "ran": False, "reason": "breaker_open"}

    rc, out = _drive(tool_mod, monkeypatch, tmp_path,
                     stored_names=["the alpha", "AB-12", "src/beta.py"],
                     trace_maker=_trace)
    assert rc == 3, f"a mixed-mode run was not refused (rc={rc})"
    assert not out.exists(), "an artifact labelled as one mode was written from a mixture"


def test_a_sample_that_starves_a_class_is_refused(tool_mod, monkeypatch, tmp_path):
    """Exit 2, and NO artifact: a per-class rate from ZERO names is a silent drop.

    `_allocate_shares(1, 3 classes)` is `{definite: 1, ident: 0, path: 0}` — a
    correct allocation that must not be sampled anyway. Deleting this refusal
    would write an artifact whose `sample_by_class` reports a class rate over no
    names, which is the class-drop the largest-remainder pass exists to prevent.
    """
    rc, out = _drive(tool_mod, monkeypatch, tmp_path,
                     stored_names=["the alpha", "AB-12", "src/beta.py"],
                     trace_maker=lambda: {"leg": "vector", "ran": True,
                                          "degraded": False},
                     argv=["--population", "3", "--sample", "1",
                           "--captures", "2", "--out", str(tmp_path / "artifact.json")])
    assert rc == 2, f"a class-starving sample was not refused (rc={rc})"
    assert not out.exists(), "an artifact was written from a sample that starved a class"


def test_a_sample_larger_than_the_population_is_refused(tool_mod, monkeypatch, tmp_path):
    """Exit 2: `sample_by_class` cannot exceed the population it samples."""
    rc, out = _drive(tool_mod, monkeypatch, tmp_path,
                     stored_names=["the alpha", "AB-12", "src/beta.py"],
                     trace_maker=lambda: {"leg": "vector", "ran": True,
                                          "degraded": False},
                     argv=["--population", "3", "--sample", "4",
                           "--captures", "2", "--out", str(tmp_path / "artifact.json")])
    assert rc == 2, f"a sample larger than the population was not refused (rc={rc})"
    assert not out.exists(), "an artifact was written from a sample larger than the population"


# ── the #12294 gate arms' ACCOUNTING ──────────


def test_the_12294_gate_arms_reconcile_against_a_driven_run(tool_mod, monkeypatch,
                                                           tmp_path):
    """The four `#12294` arms have no other test: pin their accounting to a RUN.

    `tests/test_name_entropy_12294.py` pins the ported predicate and
    `_pooled_coverage` IN ISOLATION; nothing observed what `run()` accumulates.
    Measured on this suite: narrowing the pooled loop's bound, or deleting
    `arms["pooled_coverage"] = _pooled`, left every test green while
    `pooled_gate`'s quoted share ("1 of 86 rescued at k=3, 6 of 86 at k=15" — the
    gate's production-truth cost) described a population the artifact would
    still certify as untruncated: #11823's silent-cap class, inside the
    instrument whose numbers decline a lever.

    Mutations that must red this: the loop bound no longer following
    `names_reached`; a `gated` count accumulated once per (name x predicate)
    instead of once per name; a per-k table that stops summing to its `_k_sum`;
    a `pooled_gate` cell whose `rescued_by_union + lost + unreachable_regardless`
    stops equalling its `gated` — and, separately, collapsing
    `unreachable_regardless` into `lost`, which is the pre-fix defect (it books a
    gate-independent miss as the gate's cost, inflating this PR's own evidence).
    ⛔ `rescued_by_union + lost == gated` is DELIBERATELY not the invariant here:
    the healthy driven cell is `rescued 1 + lost 16 = 17` against `gated 18`.
    """
    # The arms' found-branches all execute, so `suppressed_correct` /
    # `rescued_by_union` are exercised rather than left at their zero defaults.
    # ⛔ The population must be LARGER THAN ONE POOLED GROUP (15 names), or the
    # loop's bound is untestable: with three names `range(...)` has a single step
    # whatever the bound is, and halving `names_reached` left this test green
    # (measured).
    pop = ([{"cls": "definite", "name": f"the item{i}"} for i in range(9)]
           + [{"cls": "ident", "name": f"#{2258 + i}"} for i in range(9)]
           + [{"cls": "path", "name": f"src/mod{i}.py"} for i in range(9)])
    names = [r["name"] for r in pop]
    # The row set is chosen so all three `pooled_gate` branches are exercised at
    # once: every query returns its OWN name's row — EXCEPT `#2266`, whose row no
    # query returns, so exactly one gated name is unreachable regardless — and
    # every query ALSO returns `#2258`'s row, so a gated name whose query is
    # never issued is still RESCUED when a KEPT name's query returns it. Without
    # that asymmetry `rescued == gated` and `lost == 0` for every cell, and
    # swapping the two increments — which inverts "6 of 86 rescued" into "80 of
    # 86", i.e. the falsifier into an endorsement of the gate — left this test
    # green (measured).
    from tortoise.extractor_v2 import _sanitise_query_text

    row_by_name = {n: {"id": f"obj-11901{i:06d}", "content": n,
                       "point_kind": "core:other"}
                   for i, n in enumerate(names)}
    by_query = {_sanitise_query_text(n): n for n in names}
    _ALIAS = {"id": row_by_name["#2258"]["id"], "content": "2258",
              "point_kind": "core:other"}

    def _rows(query):
        # `#2266` is the UNREACHABLE-REGARDLESS case: no query (not even its own)
        # ever returns its row, so neither the ungated nor the gated union holds
        # it — the gate costs nothing there and it must NOT book as `lost`.
        if by_query.get(query) == "#2266":
            return [row_by_name["#2258"], _ALIAS]
        own = row_by_name.get(by_query.get(query, ""))
        rows = [own] if own is not None else []
        if row_by_name["#2258"] not in rows:
            rows.append(row_by_name["#2258"])
        # the hash-dropped surface of `#2258` (`2258`), carrying the entity's own
        # id, so exactly ONE gated VARIANT pair actually binds in phase 1 — the
        # `gate_variant` assertions below are exact because of it
        rows.append(_ALIAS)
        return rows

    rc, out = _drive(
        tool_mod, monkeypatch, tmp_path,
        # `the item0` is gated by `shipped` only (`item0` — 5 chars, 1 token);
        # `#2258` by BOTH (`2258` — 4 chars, 1 token), so neither predicate's
        # found-branches are left at their zero defaults.
        stored_names=names, pop=pop,
        trace_maker=lambda: {"leg": "vector", "ran": True, "degraded": False},
        rows_maker=_rows,
        argv=["--population", "27", "--sample", "27", "--captures", "2",
              "--out", str(tmp_path / "artifact.json")])
    assert rc == 0, f"a healthy driven run did not succeed (rc={rc})"
    arms = json.loads(out.read_text())["arms"]

    # 1. the recorded coverage describes the population the loop ACTUALLY reached
    BUDGET = 15   # mirrors `run()`'s production ``max_queries`` literal
    cov = arms["pooled_coverage"]
    assert cov["names_in_sample"] == arms["gate"]["shipped"]["names"] == 27, cov
    assert cov["names_measured"] == cov["names_reached"] - cov["skipped_singleton"]
    groups = [min(BUDGET, cov["names_reached"] - s)
              for s in range(0, cov["names_reached"], BUDGET)]
    assert len(groups) == 2, groups          # 15 + 12, so the bound is observable
    assert arms["pooled"]["3"]["captures"] == sum(1 for n in groups if n >= 2)
    assert cov["names_measured"] == sum(n for n in groups if n >= 2)
    assert cov["truncated"] is False

    # 2. per-predicate reconciliation — the three structures must agree
    for p in ("shipped", "zep_full_name"):
        g = arms["gate"][p]
        assert sum(g["suppressed_correct_by_k"].values()) == \
            g["suppressed_correct_k_sum"], p
        assert g["gated"] == sum(1 for e in arms["gate_gated_names"]
                                 if p in e["predicates"]), p
        # this row set withholds `#2266` from every window, so EXACTLY ONE gated
        # name is unreachable at any k — the falsifier's headline cannot invert,
        # and the k it would invert over is pinned by name rather than by count
        assert g["gated_and_missed"] == 1, p
        for k, cell in arms["pooled_gate"][p].items():
            # EXACT, not a sum: this fixture can rescue exactly ONE gated name
            # (`#2258` — every kept name's query returns its row; no other gated
            # name's row is ever returned by a kept query). A sum-only assertion
            # let a SWAP of the two increments pass, and that swap turns "6 of 86
            # rescued" into "80 of 86" — the falsifier into an endorsement of the
            # gate (measured: green before this assertion existed).
            assert cell["rescued_by_union"] == 1, (p, k, cell)
            # `#2266` is unreachable regardless, so it is NOT in `lost`
            assert cell["lost"] == cell["gated"] - 2, (p, k, cell)
            assert cell["unreachable_regardless"] == 1, (p, k, cell)
            assert (cell["rescued_by_union"] + cell["lost"]
                    + cell["unreachable_regardless"] == cell["gated"] > 0), (p, k)

    # 3. the FOURTH arm — `gate_variant`, the one whose denominators this file's
    # own comments record as having been wrong twice (a (surface x k) numerator
    # over a per-surface denominator, then a per-(pair x k) attach count)
    assert set(arms["gate_variant"]) == {"shipped", "zep_full_name"}
    _EXACT_GATED = {"shipped": 27, "zep_full_name": 18}
    for p in ("shipped", "zep_full_name"):
        vg = arms["gate_variant"][p]
        # the denominator is ONE window's pair count. A per-k denominator reads
        # 157/1676 (9.4 %) instead of 157/419 (37.5 %) on the corpus.
        assert vg["pairs"] == arms["variant"]["3"]["pairs"] == 36, (p, vg)
        assert vg["gated_pairs"] == _EXACT_GATED[p], (p, vg)
        # EXACTLY ONE gated pair binds today (the `#2258` -> `2258` mention).
        # Inverting the per-PAIR accumulator reports every NON-binding pair
        # instead (35); accumulating it per (pair x k) over-reports it 4x.
        assert vg["would_attach_before"] == 1, (p, vg)
        assert vg["would_attach_before_any_k"] == 1, (p, vg)
        assert 0 <= vg["would_attach_before"] <= vg["in_window_before"] \
            <= vg["gated_pairs"] <= vg["pairs"], (p, vg)


def test_the_harness_reports_how_much_of_its_own_input_it_discarded(tmp_path):
    """`harvest()` drops two kinds of record SILENTLY; it must now count them.

    Both `continue`s (an unparseable line, a record with no text) used to leave
    no trace, so an artifact could not distinguish *"the corpus lost 11k names"*
    from *"the harness stopped parsing records"* — the one distinction a re-run
    has to make. The committed artifacts predate `corpus.input` and carry none;
    this asserts the INSTRUMENT reports it, which is the part the harness owns.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location("candidate_recall_11901", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)

    day = tmp_path / "2026-01-01.jsonl"
    day.write_text(
        '{"conversation": [{"content": "the widget is broken"}]}\n'  # 1 good record
        'not json at all\n'                                          # 1 parse error
        '{"conversation": []}\n'                                     # 1 without text
        '{"conversation": [{"content": 42}]}\n',                     # content not a str
        encoding="utf-8")

    stats: dict = {}
    counts = mod.harvest(tmp_path, stats=stats)

    assert stats["lines"] == 4, "every line must be counted, including the bad ones"
    assert stats["parse_errors"] == 1
    assert stats["records_without_text"] == 2, (
        "an empty conversation AND a non-string content are both 'no text' — and "
        "both used to vanish without a trace")
    # The accounting did not change what the harvest produces.
    assert counts["definite"], "the one good record must still contribute a name"


def test_main_writes_the_input_accounting_into_the_artifact_it_emits(tmp_path, monkeypatch):
    """Drive `main()` and read the JSON it writes — not the source.

    Round 4 caught the previous version of this guard asserting SUBSTRINGS of
    `tools/candidate_recall_11901.py`, which is exactly the fail-open it was
    meant to close: resetting `_input_stats` mid-run emitted `"input": {}` and
    the suite stayed green, while renaming the variable reded a behaviour it did
    not change. A behavioural test needs no graph: the SDK is stubbed, every
    query returns no rows, and only the corpus block is read.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location("candidate_recall_11901", TOOL)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["candidate_recall_11901"] = mod
    spec.loader.exec_module(mod)

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    # The harvest rules need enough distinct definite descriptions for the
    # projection write to be worth doing; the three UNUSABLE records below are
    # what the counters must account for.
    good = "".join(
        json.dumps({"conversation": [{"content": f"the widget {i} was broken"}]})
        + "\n" for i in range(6))
    (corpus / "2026-01-01.jsonl").write_text(
        good + "not json at all\n" + json.dumps({"conversation": []}) + "\n",
        encoding="utf-8")

    class _Proj:
        """The projection surface `run()` uses: `_upsert_object` and `g.query`.

        `g.query` is part of the contract, not decoration: `run()` CHECKS that the
        names it wrote actually landed before measuring, because `o.name` is an ON
        CREATE write (#8919) — so on a REUSED graph every on-match upsert keeps the
        first run's name and the run silently measures the previous population.
        A stub that cannot answer that query dies on `proj.g`, which is precisely
        how this fake broke when the two reviewed lines met — and the suite reded
        instead of reporting a green run over a fake that cannot reach the guard.
        """

        def __init__(self):
            self._stored = []

        @property
        def g(self):
            return self

        def query(self, *_a, **_k):
            # A graph that accepted every write: the refusal is exercised by
            # `_drive`'s fakes, not here.
            return _FakeQuery(list(self._stored))

        def _upsert_object(self, row):  # stub
            self._stored.append((row["id"], row["name"]))
            return row

    class _Sdk:
        def __init__(self, **kw):
            self._proj = _Proj()

        def _get_proj(self):
            return self._proj

        def tortoise_fts_query(self, *a, **kw):
            # Every query for the FIRST name the arm asks about returns a hit, so
            # the sample has BOTH a hit and a miss. With every query empty,
            # `in_window == 0` and `exact_misses_total == recorded` under ANY
            # formula — which is what made round 4's version of this assertion
            # non-discriminating.
            if not a or a[0] == "warm":
                return []
            q = a[0]
            self._target = getattr(self, "_target", q)
            if q != self._target:
                return []
            # ⚠️ The hit must carry the id `run()` MINTED for this name: the
            # harness compares by identity, never by name (`_norm` folds case and
            # whitespace, so a name match can credit a different node). A fixed
            # "obj-1" lands in `attached` and never in `in_window` — this stub's
            # name-shaped hit was written against the other lane's comparison and
            # the premise assertion below is what caught the merge.
            # `window()` sanitises the name before the leg sees it, so the match
            # is made in the SAME sanitised form production queries with. The
            # sanitiser is imported inside `run()`, so it is not a module
            # attribute — import the same function here.
            from tortoise.extractor_v2 import _sanitise_query_text as _san
            for obj_id, name in self._proj._stored:
                if _san(name) == q:
                    return [{"id": obj_id, "content": name,
                             "kind": "point", "point_kind": "core:other"}]
            return []

        def close(self):
            pass

    import tortoise.sdk as sdk_mod
    monkeypatch.setattr(sdk_mod, "TortoiseSDK", _Sdk, raising=True)
    monkeypatch.setattr(mod, "_has_embedder", lambda: False, raising=True)
    # `run()` refuses a non-`docker://`/`redis://` URI BEFORE the stub SDK is ever
    # constructed, so this behavioural test would otherwise fail for a reason that
    # has nothing to do with what it asserts — it only passed under `test_lane.py`,
    # which exports a URI, and red under the URI-less embedded carve-out that the
    # brief advertises as an equivalent command. The gate checks the SCHEME only and
    # nothing connects: the port is deliberately dead and every query goes to `_Sdk`.
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "docker://:@127.0.0.1:1/tortoise_test_matrix")

    out = tmp_path / "report.json"
    rc = mod.main(["--sessions-dir", str(corpus), "--population", "6",
                   "--sample", "2", "--captures", "1", "--out", str(out)])
    assert rc == 0, f"main() returned {rc}"
    rep = json.loads(out.read_text())

    inp = rep["corpus"]["input"]
    assert inp["lines"] == 8, f"every input line must be counted, got {inp}"
    assert inp["parse_errors"] == 1, inp
    assert inp["records_without_text"] == 1, inp
    assert inp["files"] == 1, "the file count must be the harvest's OWN input"
    assert rep["corpus"]["files"] == 1, (
        "corpus.files must come from the harvest, not a second glob of a live dir")
    assert rep["arms"]["exact_misses_recorded"] == len(rep["arms"]["exact_misses"])
    assert rep["arms"]["exact_misses_total"] == rep["arms"]["exact_misses_recorded"], (
        "with no cap the two must be EQUAL; a re-introduced cap makes recorded < "
        "total, which is the report of the regression (see "
        "test_the_artifact_records_every_miss_so_a_cap_cannot_return)")
    # The premise of the next assertion: at least one hit, or "total == recorded"
    # holds for every formula and asserts nothing.
    assert rep["arms"]["exact"]["3"]["in_window"] >= 1, rep["arms"]["exact"]["3"]
    # The FORMULA, not a tautology: misses must be counted against the whole
    # sample, not against the 200-cap list the artifact kept.
    assert rep["arms"]["exact_misses_total"] == (
        rep["corpus"]["sample"] - rep["arms"]["exact"]["3"]["in_window"]), (
        "exact_misses_total must be sample − in_window, not len(sample) nor "
        "len(exact_misses)")


def test_the_artifact_records_every_miss_so_a_cap_cannot_return(tool_mod, monkeypatch,
                                                               tmp_path):
    """A miss cap is BIASED, and the artifact must make its return visible.

    The harness used to keep only `len(exact_misses) < 200` entries, taken from a
    `sample` built class by class (sorted) — so once it bound, it kept the early
    classes' misses and dropped the rest, and §3's crowding statistic described a
    narrower population than its own denominator claimed. `exact_misses_recorded`
    beside `exact_misses_total` is what makes that visible: with NO cap the two
    are equal, so a run where they differ reports the regression.

    This drives MORE misses than the old cap (300 > 200), which is the only
    fixture in which a returning cap would actually bind — at small samples the
    equality holds either way, so a test that only drove 2 misses could not tell
    the two harnesses apart. Mutation that must red this: restoring
    `if k == KS[0] and len(exact_misses) < 200:` in `run()`.
    """
    big = ([{"cls": "definite", "name": f"the widget {i}"} for i in range(150)]
           + [{"cls": "ident", "name": f"AB-{i:04d}"} for i in range(100)]
           + [{"cls": "path", "name": f"src/mod_{i}.py"} for i in range(50)])
    rc, out = _drive(
        tool_mod, monkeypatch, tmp_path,
        stored_names=[r["name"] for r in big],
        # a healthy hybrid leg: the refusal is not what is under test here
        trace_maker=lambda: {"leg": "vector", "ran": True, "degraded": False},
        # `tortoise_fts_query` returns [] in `_drive`'s stub, so EVERY sampled
        # name misses and the miss count is exactly the sample size. `--out` is
        # `_drive`'s own artifact path, so the returned path is the one written.
        argv=["--population", "300", "--sample", "300", "--captures", "1",
              "--out", str(tmp_path / "artifact.json")],
        pop=big)
    assert rc == 0, f"a healthy 300-name run was refused (rc={rc})"
    arms = json.loads(out.read_text())["arms"]
    assert len(arms["exact_misses"]) == 300, (
        f"every miss must be kept, got {len(arms['exact_misses'])}")
    assert arms["exact_misses_recorded"] == 300, (
        f"the artifact must record all 300 misses, got "
        f"{arms['exact_misses_recorded']} — a cap is back")
    assert arms["exact_misses_total"] == arms["exact_misses_recorded"] == 300, (
        f"total={arms['exact_misses_total']} vs "
        f"recorded={arms['exact_misses_recorded']}: with no cap these are equal")


def test_a_degenerate_run_publishes_None_not_a_zero_figure(tool_mod, monkeypatch, tmp_path):
    """A run that measured nothing must not publish `0.0` as if it measured zero.

    ⛔ MEASURED BEFORE THE FIX: a one-name population (so every pooled group falls
    below 2 and the blocker has no pair to reduce) published
    `reduction_ratio: 0.0` — and, through the same `or 0.0` shape, `0.0` for both
    pooled cost fields. `0.0` reads as "the blocker proposed zero pairs" and "a
    capture sees zero candidates", which the run never observed: it is the
    flattering-half false label `entity_blocking.reduction_ratio` returns `None`
    to prevent, and `pooled_candidates_per_capture` is exactly the figure a brief
    compares against the blocker. Restoring `or 0.0` (or any `0.0` default) reds
    this test and nothing else.
    """
    rc, out = _drive(tool_mod, monkeypatch, tmp_path,
                     stored_names=["the alpha"],
                     trace_maker=lambda: {"leg": "vector", "ran": True,
                                          "degraded": False},
                     pop=[{"cls": "definite", "name": "the alpha"}],
                     argv=["--population", "1", "--sample", "1",
                           "--captures", "2", "--out", str(tmp_path / "artifact.json")])
    assert rc == 0, f"a one-name run was refused (rc={rc})"
    rep = json.loads(out.read_text())
    mk = rep["arms"]["blocking"]["multikey"]
    #: The population is below 2, so `reduction_ratio` has nothing to measure.
    assert mk["reduction_ratio"] is None, (
        f"reduction_ratio is {mk['reduction_ratio']!r} on a run with no pair to "
        "reduce — a `0.0` here reads as 'the blocker proposed zero pairs'")
    #: Zero capture groups reached the window, so NO pooled set was measured.
    pooled = next(iter(rep["arms"]["pooled"].values()))
    assert pooled["captures"] == 0, f"expected no capture group, got {pooled['captures']}"
    fts = rep["arms"]["blocking"]["fts_only"]
    assert fts["pooled_candidates_per_capture"] is None, (
        f"pooled_candidates_per_capture is {fts['pooled_candidates_per_capture']!r} "
        "with zero capture groups — that is a measurement the run never made")
    assert fts["pooled_candidates_per_mention"] is None, (
        f"pooled_candidates_per_mention is {fts['pooled_candidates_per_mention']!r} "
        "with zero capture groups")
