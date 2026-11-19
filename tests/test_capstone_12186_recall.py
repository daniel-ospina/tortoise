"""Pins the structural facts the #12186 capstone rests on, and its artifact.

The capstone's claim is *"the merged levers are visible to a user"*, and it is
made twice: once in prose (`docs/evidence/12186-capstone/README.md`) and once in
the committed `measurement.json`. Two ways that record rots, and this module
closes both:

1. **The definitions drift.** A duplicate rate is meaningless without its rule,
   and the near-duplicate rule is a *choice* (`neardup-v1`) that a later
   "simplification" could change while every published number stayed put. The
   rule, the window arithmetic and the pin's refusals are asserted here.
2. **The README drifts away from the artifact.** Every figure the README's table
   states is recomputed from the committed artifact and compared, so the doc
   cannot publish a number the evidence does not hold.

⛔ Nothing here runs the measurement — it needs a live FalkorDB and ~10 minutes of
queries. That is deliberate (the same reason
`tests/test_candidate_recall_11901.py` gives): a test that needed a graph would be
skipped on CI and would guard nothing. What is pinned is the reasoning surface.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import capstone_12186_recall as cap

REPO_ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = REPO_ROOT / "docs" / "evidence" / "12186-capstone"
PIN = EVIDENCE / "queries.json"
ARTIFACT = EVIDENCE / "measurement.json"
README = EVIDENCE / "README.md"


# ── the near-duplicate rule ──────────────────────────────────────────────────

def test_the_key_folds_case_whitespace_and_the_separator_class():
    assert cap.near_dup_key("The  Merge-Base") == "merge base"
    assert cap.near_dup_key("the_merge/base") == "merge base"


def test_a_leading_article_is_dropped_because_it_is_the_duplicate_noun_class():
    # `#11901`'s own population contains this pair; counting it as two distinct
    # nouns would report the duplicate rate at ~0 on the class the epic is about.
    assert cap.is_near_duplicate("the diff", "diff")
    assert cap.is_near_duplicate("The Diff", "the-diff")


def test_a_different_noun_is_never_a_near_duplicate():
    # The M-1 trap from #12186's research brief: "the plan doc" vs "the play doc"
    # differ by ONE character and are unrelated nouns. A rule that bound them
    # would report a duplicate rate the product does not have.
    assert not cap.is_near_duplicate("the plan doc", "the play doc")
    assert not cap.is_near_duplicate("the verdict", "the vertical")


def test_the_jaccard_threshold_is_the_declared_one():
    # "scripts/record-review.sh" vs "scripts/record-review.test.sh" — the one real
    # near-duplicate pair #12298's pin contains, at Jaccard 2/3 ... 3/5 territory.
    # Pinning the CONSTANT as well as the behaviour, because a threshold is the
    # kind of number that moves without a test noticing.
    assert cap.NEARDUP_JACCARD == 0.8
    assert cap.NEARDUP_RULE == "neardup-v1"


def test_an_empty_name_is_never_a_duplicate_of_anything():
    assert not cap.is_near_duplicate("", "")
    assert not cap.is_near_duplicate("the ", "")


# ── the in-window arithmetic ─────────────────────────────────────────────────

def test_duplicate_in_window_counts_rows_and_pairs_separately():
    d = cap.duplicate_in_window(["the diff", "diff", "the verdict"])
    assert d == {"rows": 3, "duplicate_rows": 2, "pairs": 1}


def test_duplicate_in_window_is_zero_when_nothing_repeats():
    assert cap.duplicate_in_window(["alpha", "beta"])["duplicate_rows"] == 0


def test_duplicate_in_window_does_not_double_count_a_three_way_cluster():
    # Three mutually near-duplicate rows are THREE rows redundant, not three
    # pairs counted as three rows (the over-report shape this module's siblings
    # have hit before).
    d = cap.duplicate_in_window(["the diff", "diff", "the-diff"])
    assert d["duplicate_rows"] == 3
    assert d["pairs"] == 3


# ── the pin, and its refusals ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def pin() -> dict:
    if not PIN.exists():
        pytest.skip(f"no committed pin at {PIN}")
    return json.loads(PIN.read_text())


def test_the_committed_pin_is_the_schema_this_tool_measures(pin):
    assert pin["schema"] == cap.PIN_SCHEMA
    assert pin["rules"]["sample"] == len(pin["query_ids"])
    assert pin["rules"]["population"] == len(pin["population_rows"])


def test_the_pin_digest_recomputes_from_its_own_rows(pin):
    rows = pin["population_rows"]
    assert pin["digest"] == hashlib.sha256(
        "\n".join(r["name"] for r in rows).encode()).hexdigest()[:16]
    by_id = {r["id"]: r for r in rows}
    assert pin["query_digest"] == hashlib.sha256(
        "\n".join(by_id[i]["name"] for i in pin["query_ids"]).encode()
    ).hexdigest()[:16]


def test_every_query_id_is_a_population_row_and_no_class_is_starved(pin):
    ids = set()
    by_cls: dict[str, int] = {}
    for r in pin["population_rows"]:
        assert r["id"] not in ids, f"duplicate id {r['id']}"
        ids.add(r["id"])
        by_cls[r["cls"]] = by_cls.get(r["cls"], 0) + 1
    assert set(pin["query_ids"]) <= ids
    assert set(by_cls) == set(pin["rules"]["shares"])
    assert all(v > 0 for v in pin["rules"]["shares"].values())
    assert sum(pin["rules"]["shares"].values()) == len(pin["query_ids"])


def test_a_pin_of_another_schema_is_refused(tmp_path):
    bad = tmp_path / "pin.json"
    bad.write_text(json.dumps({"schema": "something-else",
                               "population_rows": [{"id": "a", "name": "x",
                                                    "cls": "definite"}],
                               "query_ids": ["a"]}))
    with pytest.raises(SystemExit, match="schema"):
        cap.load_pin(bad)


def test_a_pin_whose_query_id_is_absent_from_the_population_is_refused(tmp_path):
    bad = tmp_path / "pin.json"
    bad.write_text(json.dumps({
        "schema": cap.PIN_SCHEMA,
        "population_rows": [{"id": "a", "name": "x", "cls": "definite"}],
        "query_ids": ["a", "ghost"]}))
    with pytest.raises(SystemExit, match="absent from"):
        cap.load_pin(bad)


def test_build_pin_refuses_to_overwrite_without_force(tmp_path, monkeypatch):
    """A 'fixed' query set that any run can re-freeze is not fixed."""
    import argparse

    existing = tmp_path / "pin.json"
    existing.write_text("{}")
    args = argparse.Namespace(sessions_dir=str(tmp_path), population=10,
                              sample=3, out=str(existing), force=False)
    assert cap.build_pin(args) == 3
    assert existing.read_text() == "{}"


# ── the comparisons block ────────────────────────────────────────────────────

def test_the_artifact_refuses_the_cross_corpus_comparison_by_name():
    """The 0.7133 baseline and #11901's cells are DIFFERENT populations.

    A capstone that prints 0.93 beside 0.7133 invites the reader to subtract
    them. `_comparisons` names both and says why not, so the refusal travels with
    the artifact instead of living in a reviewer's memory.
    """
    comp = cap._comparisons({})
    named = " ".join(comp["different_corpus_from"])
    assert "0.7133" in named
    assert "#11901" in named
    assert comp["same_corpus_as"] == []


def test_the_comparisons_block_accepts_only_within_run_pairs():
    comp = cap._comparisons({})
    joined = " ".join(comp["within_run_comparisons_that_ARE_valid"])
    assert "capture_prior -> user_search" in joined
    assert "user_search_rerank" in joined


# ── the user door ────────────────────────────────────────────────────────────

def test_the_measured_door_is_the_one_the_search_tool_is_registered_to():
    """`tortoise_search` is the MCP tool and REST `GET /v1/search`.

    The arms call `sdk.tortoise_fts_query`; this asserts the registry still
    binds that tool to that verb, so "we measured the user's door" is checked
    rather than asserted in prose.
    """
    pytest.importorskip("tortoise.tool_registry")
    binding = cap._user_door_binding()
    assert binding["present"] is True
    assert binding["sdk_method"] == "tortoise_fts_query"
    assert binding["matches_measured_call"] is True
    assert binding["rest"] == "GET /v1/search"


# ── the artifact (skipped until the run lands) ───────────────────────────────

@pytest.fixture(scope="module")
def artifact() -> dict:
    if not ARTIFACT.exists():
        pytest.skip(f"no committed artifact at {ARTIFACT}")
    return json.loads(ARTIFACT.read_text())


def _arm_names() -> list[str]:
    return [arm for arm, _door, _env in cap.ARMS]


def test_the_artifact_measures_every_arm_the_capstone_quotes(artifact):
    for arm in _arm_names():
        assert arm in artifact["arms"], arm
    assert "mint_rate" in artifact["arms"]
    assert artifact["ks"] == list(cap.KS)


def test_recall_is_monotone_in_k_on_every_arm(artifact):
    for surface in _arm_names():
        recall = artifact["arms"][surface]["recall"]
        values = [recall[str(k)] for k in cap.KS]
        assert values == sorted(values), (surface, values)
        assert 0.0 <= values[0] <= values[-1] <= 1.0


def test_the_duplicate_rate_never_exceeds_the_window(artifact):
    for surface in _arm_names():
        dup = artifact["arms"][surface]["duplicate_rate_in_window"]
        for k in cap.KS:
            cell = dup[str(k)]
            assert cell["duplicate_rows"] <= cell["rows"]
            assert cell["rate"] is None or 0.0 <= cell["rate"] <= 1.0


def test_the_artifact_carries_the_pin_identity_that_makes_it_reproducible(artifact):
    pin = artifact["pin"]
    assert pin["schema"] == cap.PIN_SCHEMA
    assert pin["digest"] and pin["query_digest"] and pin["sha256"]
    assert pin["queries"] == len(json.loads(PIN.read_text())["query_ids"])


def test_the_artifact_states_the_shipped_state_of_the_rerank_lever(artifact):
    """`user_search` must be the DEFAULT door, and the default is OFF.

    If the ambient gate were armed, `user_search` would itself be a rerank and
    the two user arms would be the same measurement twice — a zero that looks
    exactly like a real finding.
    """
    vis = artifact["merged_lever_visibility"]
    assert vis["fused_rerank_shipped_default"] == "OFF"
    assert vis["fused_rerank_ran_in_user_search"] is False
    assert artifact["arms"]["user_search_rerank"]["rerank_entries_ran"] > 0


# ── the paired comparisons ───────────────────────────────────────────────────

def test_every_declared_pair_names_two_real_arms_and_a_lever():
    names = {arm for arm, _door, _env in cap.ARMS}
    for before, after, lever in cap.PAIRS:
        assert before in names, before
        assert after in names, after
        assert lever.strip()


def _synthetic_arm(ranks: dict[str, int | None], degraded: set[str]) -> dict:
    return {"per_query": {pid: {"rank": rank, "degraded": pid in degraded,
                                "cls": "definite"}
                          for pid, rank in ranks.items()}}


def test_the_paired_comparison_excludes_a_window_degraded_on_either_side():
    """A leg `timeout` is not the lever: it must leave the paired denominator."""
    before = _synthetic_arm({"a": 1, "b": 9, "c": 1}, degraded=set())
    after = _synthetic_arm({"a": 1, "b": 1, "c": None}, degraded={"b"})
    result = cap.paired(before, after, k=3)
    # `b` improved only because its window degraded on the after side; excluding
    # it is the difference between "the lever works" and "a leg timed out".
    assert result["queries_compared"] == 2
    assert result["excluded_degraded_on_either_side"] == 1
    assert result["gains"] == 0
    assert result["losses"] == 1
    assert result["delta_pts_clean"] == -50.0


def test_the_paired_block_in_the_artifact_accounts_for_every_query(artifact):
    for name, block in artifact["paired"].items():
        cell = block["paired"]
        assert cell["queries_compared"] + cell[
            "excluded_degraded_on_either_side"] == artifact["pin"]["queries"], name
        assert cell["gains"] + cell["losses"] <= cell["queries_compared"], name


# ── the README, against the artifact ─────────────────────────────────────────

def test_the_readme_quotes_the_recall_the_artifact_holds(artifact):
    if not README.exists():
        pytest.skip(f"no README at {README}")
    text = README.read_text()
    for arm in _arm_names():
        for k in cap.KS:
            value = artifact["arms"][arm]["recall"][str(k)]
            assert f"{value:.4f}" in text, f"README does not quote {arm}@{k}={value}"


def test_the_readme_quotes_the_paired_deltas_the_artifact_holds(artifact):
    text = README.read_text()
    for name, block in artifact["paired"].items():
        cell = block["paired"]
        assert f"{abs(cell['delta_pts_clean']):.2f}" in text, (
            f"README does not quote the paired delta for {name} "
            f"({cell['delta_pts_clean']})")


def test_the_readme_quotes_the_duplicate_rates_the_artifact_holds(artifact):
    text = README.read_text()
    for k in cap.KS:
        rate = artifact["arms"]["user_search"][
            "duplicate_rate_in_window"][str(k)]["rate"]
        assert f"{rate:.4f}" in text, f"README does not quote the dup rate at k={k}"


def test_the_readme_quotes_the_mint_rates_the_artifact_holds(artifact):
    text = README.read_text()
    mint = artifact["arms"]["mint_rate"]
    for cell in (mint["shipped"]["rate"], mint["norm_only"]["rate"]):
        assert f"{cell:.4f}" in text, f"README does not quote mint rate {cell}"
    for cls, row in mint["by_class"].items():
        assert f"{row['shipped_mint_rate']:.4f}" in text, (
            f"README does not quote the {cls} mint rate")


def test_the_readme_refuses_the_cross_corpus_comparison_too(artifact):
    """The artifact refuses it; the prose must not invite it either."""
    text = README.read_text()
    assert "0.7133" in text
    assert "refuses that comparison" in text or "not a comparison to the 0.7133" in text
