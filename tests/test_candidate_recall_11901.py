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
ARTIFACT = DOC_DIR / "measurement.json"


# ── the artifact ─────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def report() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


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


def test_the_shipped_matcher_attaches_no_variant_at_any_width(report):
    """The load-bearing negative: width is not the lever for the variant class.

    ``_find_existing_entity`` is exact-``_norm`` equality, so a surface variant
    is unattachable at EVERY k. If this ever becomes non-zero, the brief's §4
    and §5 tables are wrong and the whole recommendation must be re-derived.
    """
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


# ── the brief's tables must equal the artifact ──────────────────────────────

def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", line):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_the_brief_quotes_the_recall_the_artifact_holds(report):
    """The §3 table's recall column is recomputed from the evidence."""
    text = BRIEF.read_text()
    n = report["corpus"]["sample"]
    for k in ("3", "5", "10", "15"):
        row = report["arms"]["exact"][k]
        assert f"{row['in_window']}/{n} = {100 * row['in_window'] / n:.1f} %" in text, (
            f"k={k}: the brief does not state {row['in_window']}/{n} — "
            "the table and the artifact have diverged")
        assert row["miss_absent_at_max_k"] == report["arms"]["exact"]["15"]["miss_absent_at_max_k"]


def test_the_brief_quotes_the_variant_rates_the_artifact_holds(report):
    text = BRIEF.read_text()
    variant = report["arms"]["variant"]
    for k in ("3", "15"):
        row = variant[k]
        pct = 100 * row["in_window"] / row["pairs"]
        assert f"{row['in_window']} = {pct:.1f} %" in text, (
            f"k={k}: the brief does not state {row['in_window']} = {pct:.1f}% in-window")
        assert row["attached"] == 0 and "**0 = 0.0 %**" in text


def _squash(text: str) -> str:
    """Whitespace-insensitive view: a line wrap must not fail a figure check."""
    return re.sub(r"\s+", " ", text)


def test_the_brief_quotes_the_pooled_set_the_artifact_holds(report):
    pooled = report["arms"]["pooled"]["3"]
    mean = pooled["set_sum"] / pooled["captures"]
    wanted = (f"mean {mean:.1f} candidates, min {pooled['set_min']}, "
              f"max {pooled['set_max']}")
    assert wanted in _squash(BRIEF.read_text()), (
        f"the brief does not state the pooled set ({wanted})")


def test_the_brief_keeps_its_two_standing_negatives():
    """Promises a future lane must not quietly break while editing the doc."""
    text = BRIEF.read_text()
    # The LongMemEval run report stays PENDING; this doc must never claim a figure.
    assert "PENDING" in text and "no LongMemEval figure of our own is claimed" in text
    # The KEEP ruling is quoted, never re-opened.
    assert "No pass may remove those embeddings" in text
    assert "reopen" in text
