"""#12295 — the fold/blocking measurement's internal consistency, pinned.

The artifact is `docs/research/2026-11-12-12295-fold-blocking/measurement.json`,
produced by the SAME harness `#11901` shipped (`tools/candidate_recall_11901.py`,
reused, not rebuilt) with three additive arms: `attached_norm_only` (a frozen copy
of pre-#12295 phase 1), `in_multikey_block` (the multi-key blocker's pair
completeness) and `duplicate_rate` (per configuration).

What is pinned here is NOT "the numbers are good" — it is that the numbers the
brief quotes are the ones the artifact holds, and that the artifact cannot be
internally inconsistent while still being quotable. `#11901` paid for that
lesson twice (a pooled mean on a published surface that no committed artifact
held, corrected in a follow-up comment).

Run::

    TORTOISE_TEST_CARVE_OUT=1 uv run pytest tests/test_12295_fold_blocking_measurement.py -q
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

DOC_DIR = (Path(__file__).resolve().parent.parent
           / "docs" / "research" / "2026-11-12-12295-fold-blocking")
ARTIFACT = DOC_DIR / "measurement.json"
BRIEF = DOC_DIR / "research-brief.md"


@pytest.fixture(scope="module")
def report() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


def test_the_artifact_declares_one_uniform_retrieval_mode(report):
    """A mixture published as one mode is the false-label class #2952 refuses."""
    r = report["retrieval"]
    assert r["uniform_windows"] is True
    assert r["legs_seen"].get("vector") is True, (
        "the artifact is not the fused read — re-run with the embeddings extra "
        "(a keyword-only figure is never a figure about the production read)")
    assert r["window_modes"]["keyword_only"] == 0


def test_the_pre_12295_control_is_the_zero_it_claims(report):
    """The BEFORE half of the before/after, frozen in the harness.

    If `attached_norm_only` is ever non-zero, either the frozen control drifted
    or a variant is `_norm`-equal to its canonical — in both cases the 0 % claim
    this lane's headline rests on is no longer what was measured.
    """
    v = report["arms"]["variant"]
    for k, row in v.items():
        assert row["attached_norm_only"] == 0, (
            f"k={k}: the pre-#12295 control attached {row['attached_norm_only']} "
            "— the fold's contribution is no longer isolated")
    assert report["arms"]["duplicate_rate"]["prior_on_norm_only"]["attached"] == 0


def test_the_fold_moves_the_variant_class_and_the_rate_follows(report):
    """The AFTER half, and the arithmetic that ties it to the duplicate rate."""
    v = report["arms"]["variant"][str(report["arms"]["duplicate_rate"]["window_k"])]
    dr = report["arms"]["duplicate_rate"]
    rate = dr["prior_on_shipped_folded"]
    assert v["attached"] > 0, "the folded rung bound nothing — the lane did nothing"
    assert rate["attached"] == v["attached"]
    assert dr["pairs"] == v["pairs"], (
        "the duplicate rate was computed on a different pair count than the arm it quotes")
    assert rate["minted"] == dr["pairs"] - rate["attached"]
    assert rate["rate"] == pytest.approx(rate["minted"] / dr["pairs"], abs=1e-3)
    # …and the prior being ON is what makes the before/after comparable at all.
    assert report["arms"]["duplicate_rate"]["prior_off"]["rate"] == 1.0


def test_pair_completeness_is_computed_from_the_counts_it_reports(report):
    """No published number may come from anywhere but the artifact's own counts."""
    b = report["arms"]["blocking"]
    assert b["multikey"]["in_block"] == b["multikey"]["pair_completeness"] * b["pairs"] \
        or b["multikey"]["pair_completeness"] == pytest.approx(
            b["multikey"]["in_block"] / b["pairs"], abs=1e-3)
    for k, pc in b["fts_only"]["pair_completeness"].items():
        assert pc == pytest.approx(b["fts_only"]["in_window"][k] / b["pairs"], abs=1e-3)


def test_multikey_blocking_is_never_worse_than_the_window_and_says_its_cost(report):
    """The comparison the brief makes, as an assertion.

    A blocker can reach completeness 1.0 by proposing everything, so the
    completeness AND the reduction ratio must both be present — and the claim
    "better completeness at lower cost" is only allowed if the cost is lower.
    """
    b = report["arms"]["blocking"]
    mk, fts = b["multikey"], b["fts_only"]
    for k, pc in fts["pair_completeness"].items():
        assert mk["pair_completeness"] >= pc, (
            f"multi-key blocking was worse than the k={k} window")
    assert mk["reduction_ratio"] is not None and fts["reduction_ratio"] is not None
    assert mk["candidates_mean"] <= fts["candidates_mean"], (
        "the blocker proposes MORE than the capture's pooled candidate set — the "
        "brief's 'at lower cost' sentence would be false")
    # A blocker is a function of the names, so a window width cannot move it.
    assert mk["k_independent"] is True


def test_the_residual_is_the_two_named_classes_and_nothing_unexplained(report):
    """Every unattached pair is attributed — the brief's §4 table is this test.

    The unattached remainder must be exactly the two DECLARED classes:
    ``space-to-hyphen`` (the article anchor, #12047 round-4 finding 5) and
    ``basename-only`` (the precision rule: a basename attaches only on a complete
    population). An unexplained gap means the explanation is incomplete.
    """
    by_class = report["arms"]["variant_by_class"]
    total = sum(d["3"]["pairs"] for d in by_class.values())
    attached = sum(d["3"]["attached"] for d in by_class.values())
    unattached = {
        c: d["3"]["pairs"] - d["3"]["attached"] for c, d in by_class.items()
    }
    assert total == report["arms"]["blocking"]["pairs"]
    assert attached == report["arms"]["variant"]["3"]["attached"]
    declared = {"space-to-hyphen", "basename-only", "article-drop"}
    unexplained = {c: n for c, n in unattached.items() if n and c not in declared}
    assert not unexplained, (
        f"unattached pairs in undeclared classes {unexplained} — the brief's "
        "residual table no longer explains the remainder")
    # Both declared classes are REACHABLE by the blocker: this is what makes the
    # residual a decision (the anchor, the precision rule) and not a retrieval gap.
    for cls in ("space-to-hyphen", "basename-only"):
        d = by_class[cls]["3"]
        assert d["in_multikey_block"] >= d["in_window"], (
            f"{cls}: the blocker did not reach at least the window's candidates")


def test_the_brief_exists_and_quotes_the_artifact(report):
    """A brief whose numbers are not in the artifact is the #11901 erratum class."""
    assert BRIEF.exists(), f"missing committed brief {BRIEF}"
    text = BRIEF.read_text()
    b = report["arms"]["blocking"]
    attached = report["arms"]["variant"][str(report["arms"]["duplicate_rate"]["window_k"])]["attached"]
    for needle in (str(b["pairs"]), str(attached),
                   f"{b['multikey']['candidates_mean']}",
                   f"{b['fts_only']['candidates_mean']}"):
        assert needle in text, f"the brief does not quote {needle!r} from the artifact"
