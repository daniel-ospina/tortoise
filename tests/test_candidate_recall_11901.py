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


# ── the artifact ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def control() -> dict:
    assert CONTROL.exists(), f"missing committed control {CONTROL}"
    return json.loads(CONTROL.read_text())


def test_both_artifacts_declare_which_retrieval_mode_ran(report, control):
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
    assert "KEYWORD-ONLY" in control["retrieval"]["note"]
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

    assert mod._mode_note(True, {"fts": True, "vector": True}) == "hybrid"
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


def test_the_matcher_is_exact_norm_equality_plus_kind():
    """§4/§5's premise: a surface variant is invisible to phase 1.

    ``_norm`` is whitespace-collapse + lowercase and nothing else, so a mention
    differing by an article or a separator does not match even when the node is
    in the window — which is why the fold belongs in the comparison and NOT in
    ``_norm`` (whose other consumers assume exactly this narrow contract).
    """
    from tortoise.extractor_v2 import _find_existing_entity, _norm

    assert _norm("The  Plan-Doc") == "the plan-doc"
    existing = [{"id": "obj-1", "name": "the plan doc", "kind": "core:other"}]
    assert _find_existing_entity(existing, "the plan doc", "core:other")[1] == "exact"
    for variant in ("plan doc", "the-plan-doc", "the plan-doc"):
        ex, mode = _find_existing_entity(existing, variant, "core:other")
        assert ex is None, f"{variant!r} resolved — the brief's 0% arm is stale"
        assert mode == "none"


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
    for art, label in ((report, "hybrid"), (control, "keyword-only")):
        n = art["corpus"]["sample"]
        for k in ("3", "5", "10", "15"):
            row = art["arms"]["exact"][k]
            pct = 100 * row["in_window"] / n
            want = (f"| {k} | {row['in_window']}/{n} = {pct:.1f} % | "
                    f"{row['miss_past_k']} | {row['miss_absent_at_max_k']} | "
                    f"{row['rows_zero']} |")
            assert want in text, f"{label} k={k}: the brief does not carry `{want}`"
    # §3's prose figures on the same misses, recomputed from the k=3 row.
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


def test_the_brief_keeps_its_two_standing_negatives():
    """Promises a future lane must not quietly break while editing the doc."""
    text = BRIEF.read_text()
    # The LongMemEval run report stays PENDING; this doc must never claim a figure.
    assert "PENDING" in text and "no LongMemEval figure of our own is claimed" in text
    # The KEEP ruling is quoted, never re-opened.
    assert "No pass may remove those embeddings" in text
    assert "reopen" in text
