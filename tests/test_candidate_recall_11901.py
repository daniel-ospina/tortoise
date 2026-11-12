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
    assert (control["retrieval"]["note"]
            == mod._mode_note(False, control["retrieval"]["legs_seen"])), (
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
    assert note["note"] == mod._mode_note(note["has_embedder"], note["legs_seen"]), (
        f"the hybrid artifact's note contradicts the legs it declares: "
        f"{note['note']!r} vs {mod._mode_note(note['has_embedder'], note['legs_seen'])!r}")
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
    # review round mutated them with a green suite — the "derived figure, not
    # the stored one" class the k=3 mean-rank guard above already closed for
    # ONE of the two ranks.
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
    # they were simply never pinned, which is how §9 could claim the artifact
    # carried no corpus census at all.
    d_hyb = report["corpus"]["harvested_distinct"]["definite"]
    d_ctl = control["corpus"]["harvested_distinct"]["definite"]
    drift = 100 * (d_ctl - d_hyb) / d_hyb
    assert f"{d_hyb:,} (hybrid) and {d_ctl:,} (control)" in text, (
        f"§2 does not state the harvested_distinct pair ({d_hyb:,} / {d_ctl:,})")
    assert f"a {drift:.2f} % drift" in text, (
        f"§2 does not state the recomputed between-run drift ({drift:.2f} %)")

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
