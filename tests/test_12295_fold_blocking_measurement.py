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
import re
import subprocess
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
    assert report.get("measured_for") == 12295, (
        "the artifact does not name the issue it is evidence for")
    #: The run MUST be identifiable from the brief alone. Without these three the
    #: brief can describe a different run than the artifact holds — which it did
    #: (#12295 round-2 B2): the header named elapsed/graph/digest from the run
    #: BEFORE the reviewer fixes, so a reader reproducing via the digest landed on
    #: a population the artifact does not contain.
    brief = BRIEF.read_text()
    corpus = report["corpus"]
    for field in ("graph", "population_digest", "elapsed_s"):
        assert str(corpus[field]) in brief, (
            f"the brief does not name the artifact's corpus.{field} "
            f"({corpus[field]!r}) — the header describes a different run")
    assert report.get("code_head"), "the artifact does not name the commit it measured"
    #: ⛔ A TRUTHY HASH IS NOT A REACHABLE ONE. The #12304 rebase rewrote the
    #: measured commit out of every ref, and this assertion was the only thing
    #: guarding the field — so the artifact named an object no fresh clone has and
    #: nothing redded. Assert it names a real commit in THIS history, where git and
    #: the history are available (a shallow CI checkout may legitimately not have
    #: it, and an over-block there is the failure this guard must not add).
    assert re.fullmatch(r"[0-9a-f]{40}", str(report["code_head"])), (
        f"code_head is not a full commit sha: {report['code_head']!r}")
    repo = Path(__file__).resolve().parent.parent
    if (repo / ".git").exists():
        #: ⛔ EVERY git subprocess here needs a timeout: on a promisor/partial
        #: clone an ABSENT object makes `git cat-file -e` spawn a lazy fetch whose
        #: negotiation can burn minutes before failing (MEMORY.md
        #: `[git-exit-code-is-not-proof]`: 5m46s of CPU, suite aborted at pytest's
        #: 280 s faulthandler). A timeout is "could not answer", which this guard
        #: must treat exactly like a shallow checkout — never as a red.
        try:
            present = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e",
                 f"{report['code_head']}^{{commit}}"],
                capture_output=True, timeout=10)
        except subprocess.TimeoutExpired:
            present = None
        if present is not None and present.returncode == 0:
            try:
                ancestor = subprocess.run(
                    ["git", "-C", str(repo), "merge-base", "--is-ancestor",
                     report["code_head"], "HEAD"], capture_output=True, timeout=10)
            except subprocess.TimeoutExpired:
                ancestor = None
            #: 0 = ancestor, 1 = not an ancestor, anything else = could not answer.
            if ancestor is not None and ancestor.returncode in (0, 1):
                assert ancestor.returncode == 0, (
                    f"code_head {report['code_head']} exists but is not in this "
                    "branch's history — a rebase rewrote it out of every ref, so "
                    "the artifact cannot be tied to a tree a fresh clone has "
                    "(name the rebased commit and record the docstring-only diff)")
            assert report.get("code_head_note"), (
                "code_head is a rebased stand-in but the artifact does not say so; "
                "the note names the commit actually measured and the diff it is "
                "equivalent to")
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


def test_the_blocker_is_NOT_earned_on_this_corpus_and_the_artifact_says_so(report):
    """N-3's falsifier F1, pinned as the result it is.

    The shipped window reaches pair completeness 1.0 at k=15 with 15 candidates
    per mention; the multi-key blocker reaches 1.0 at ~35. If that ever flips —
    the window losing completeness the blocker keeps at a comparable cost — the
    brief's "not earned for the capture prior" verdict is wrong and must be
    re-derived. Pinned in BOTH directions so neither sentence can rot:
    completeness is never worse than the window, AND it is not cheaper.
    """
    b = report["arms"]["blocking"]
    mk, fts = b["multikey"], b["fts_only"]
    for k, pc in fts["pair_completeness"].items():
        assert mk["pair_completeness"] >= pc, (
            f"multi-key blocking was worse than the k={k} window")
    assert fts["pair_completeness"]["15"] == 1.0, (
        "the window no longer reaches completeness 1.0 at k=15 — the 'not "
        "earned' verdict rested on that")
    # The COST comparison must be per mention, in one unit; the pooled capture
    # figure is recorded separately precisely so it cannot be used as one.
    assert mk["candidates_mean_per_mention"] > fts["candidates_mean_per_mention"]["15"], (
        "the blocker is now cheaper per mention than the k=15 window — re-derive "
        "the verdict, it may now be earned")
    assert fts["pooled_candidates_per_capture"] > fts["pooled_candidates_per_mention"], (
        "a capture's union is smaller than one mention's — the two quantities were "
        "swapped somewhere")
    assert mk["k_independent"] is True


def test_the_residual_is_the_two_named_classes_and_nothing_unexplained(report):
    """Every unattached pair is attributed — the brief's §4 table is this test.

    The unattached remainder must be exactly the DECLARED classes:
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
    # Three declared classes: the two residuals carry the explanation, the third
    # is the pre-existing retrieval miss #11901 owns (out of window at k=3).
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
                   f"{b['multikey']['candidates_mean_per_mention']}",
                   f"{b['fts_only']['pooled_candidates_per_capture']}",
                   str(report["arms"]["variant"]["3"]["attached_folded"]),
                   f"{b['multikey']['reduction_ratio']}"):
        assert needle in text, f"the brief does not quote {needle!r} from the artifact"


def test_the_per_k_width_claim_matches_the_artifact(report):
    """The brief's article-anchor section turns on ONE class's per-`k` rows.

    ⛔ A substring needle cannot see this: `"180"`, `"179"`, `"207"` and `"386"`
    occur throughout the prose, so a brief quoting a SUPERSEDED row would stay
    green while its central claim went stale — the #11901 erratum class this file
    exists to prevent. So assert the artifact's shape, then assert the brief
    quotes the figures derived from it.
    """
    cls = report["arms"]["variant_by_class"]["space-to-hyphen"]
    ks = ("3", "5", "10", "15")
    pairs = {cls[k]["pairs"] for k in ks}
    assert len(pairs) == 1, (
        f"the class population moved between ``k`` ({sorted(pairs)}) — the rows "
        "are no longer a window sweep of ONE population")
    total = pairs.pop()
    windows = [cls[k]["in_window"] for k in ks]
    assert windows == sorted(windows), (
        f"a wider window cannot lose a member, measured {windows} at k={ks}")
    assert windows[-1] == total, (
        f"the widest window holds {windows[-1]} of {total} — the brief's "
        "``width therefore cannot recover them`` is false once a member is "
        "still out of reach")
    assert [cls[k]["attached"] for k in ks] == [0] * len(ks), (
        "some member of the class attached, so the article anchor is not the "
        "sole cause — the brief's diagnosis is then wrong")
    text = BRIEF.read_text()
    assert f"{windows[0]} of {total}" in text, (
        f"the brief does not quote the narrowest window ({windows[0]} of {total})")
    assert f"all {windows[-1]} are in the window" in text, (
        f"the brief does not quote the widest window (all {windows[-1]})")
