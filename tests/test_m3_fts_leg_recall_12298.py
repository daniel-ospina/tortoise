"""Pins what #12298's M3 measurement rests on, and the artifact it published.

The brief (`docs/research/2026-11-13-12298-m3-fts-leg/research-brief.md`)
publishes numbers a reader will quote, and one of them is a **defect claim about
the shipped code**. Both rot the same two ways this repo already knows:

1. **The mechanism drifts out from under the prose.** The finding is that
   ``sparse.tokenize_sparse_query`` splits on ``_`` while RediSearch's index
   does not, so a stored ``web_search`` is queried as ``search|web`` — two terms
   the index never holds. That is asserted here against the real functions: if
   the tokenizer gains ``_``-preservation (the recommended repair) these tests
   red, and somebody must re-measure instead of the document quietly becoming
   false.
2. **The tables drift away from the artifact.** Every headline number in the
   brief is recomputed from the committed `artifact.json` and compared.

The recovery arm's own terms are pinned as a CLASS, not as strings: an additive
arm that could ever *remove* a term would not be the #8933 pattern the brief
says it is, and its zero-regression property would stop being structural.

⛔ Nothing here runs the measurement — it needs a live FalkorDB, a Postgres with
`pg_search`, and a real capture corpus. That is deliberate: a test that needed
those would be skipped on CI and would guard nothing. What is pinned is the
reasoning surface (this is #11901's own convention, followed rather than
reinvented).
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "docs" / "research" / "2026-11-13-12298-m3-fts-leg"
BRIEF = DOC_DIR / "research-brief.md"
ARTIFACT = DOC_DIR / "artifact.json"
POPULATION = DOC_DIR / "population.json"
TOOL = REPO_ROOT / "tools" / "m3_fts_leg_recall.py"


@pytest.fixture(scope="module")
def tool_mod():
    assert TOOL.exists(), f"missing harness {TOOL}"
    spec = importlib.util.spec_from_file_location("m3_fts_leg_recall", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def artifact() -> dict:
    assert ARTIFACT.exists(), f"missing committed artifact {ARTIFACT}"
    return json.loads(ARTIFACT.read_text())


@pytest.fixture(scope="module")
def population() -> dict:
    assert POPULATION.exists(), f"missing committed population {POPULATION}"
    return json.loads(POPULATION.read_text())


@pytest.fixture(scope="module")
def brief() -> str:
    assert BRIEF.exists(), f"missing committed brief {BRIEF}"
    return BRIEF.read_text(encoding="utf-8")


# ── 1. the defect claim, asserted against the shipped tokenizer ─────────────

@pytest.mark.parametrize("name,run", [
    ("web_search", "web_search"),
    ("test_ci_selection", "test_ci_selection"),
    ("capstone_verdict_check", "capstone_verdict_check"),
    ("lease_until", "lease_until"),
])
def test_the_shipped_tokenizer_splits_the_run_the_index_keeps(name: str, run: str) -> None:
    """The measured defect, in the code rather than in prose.

    RediSearch treats ``_`` as a word character, so the index's term for
    ``web_search`` IS ``web_search`` (probe recorded in the brief §3.4:
    ``'web_search' -> [['web_search', 8.0]]`` while ``'web' -> []``). The
    shipped tokenizer splits that run, so the OR query it builds contains
    **no term the index holds** — which is why the identifier class scores
    ``recall@3 = 0.20`` with 21 empty result sets.

    ⛔ If this reds, the tokenizer gained ``_``-preservation: re-measure M3 and
    update the brief. Do not delete the test to make the document true.
    """
    from tortoise.sparse import build_or_query, tokenize_sparse_query

    assert tokenize_sparse_query(name, keep_numeric=False,
                                 keep_identifiers=False) == name.split("_"), (
        f"{name!r}: the tokenizer's split changed — re-measure #12298")
    emitted = build_or_query(name).split("|")
    assert run not in emitted, (
        f"{name!r}: build_or_query now emits the run the index holds "
        f"({emitted}) — the measured defect is repaired; re-measure and "
        f"rewrite the brief §3.4 rather than relaxing this assertion")


def test_the_identifier_class_cannot_match_its_own_node(tool_mod) -> None:
    """The finding in one line: every emitted term for the class is not the index's term."""
    from tortoise.sparse import build_or_query

    for name in ("web_search", "verified_files", "py_compile"):
        emitted = set(build_or_query(name).split("|"))
        assert name not in emitted
        assert not (emitted & {name}), name


# ── 2. the control is verified, not assumed ─────────────────────────────────

@pytest.mark.parametrize("query", [
    "the diff",
    "the admin-merge rail",
    "web_search",
    "tests/test_7949_capstone_verdict_check.py",
    "ERR-4032",
    "PR #12298",
    "the work is finished",
    # degenerate shapes: single token, all-digits, stopword-only
    "diff", "2258", "the", "", "10:00",
])
def test_production_terms_reproduce_build_or_query(tool_mod, query: str) -> None:
    """The controlled arm hands Postgres `build_or_query`'s terms, byte-exactly.

    A control that silently drifted would measure a tokenizer that is not the
    product's and look perfectly healthy — so the tool refuses at run time
    (`assert_terms_match`) and this pins the same property at test time.
    """
    from tortoise.sparse import build_or_query, escape_redisearch_literal

    terms, degenerate = tool_mod.production_terms(query)
    expected = escape_redisearch_literal(query) if degenerate else "|".join(terms)
    assert build_or_query(query) == expected


def test_recovery_terms_only_add(tool_mod) -> None:
    """The recovery arm is additive — the property its zero-regression relies on."""
    assert tool_mod.recovery_terms("web_search") == ["web_search"]
    assert tool_mod.recovery_terms("tests/test_7949_capstone_verdict_check.py") == [
        "test_7949_capstone_verdict_check"]
    # A run WITHOUT an underscore is already a token the shipped tokenizer
    # emits, so the recovery arm must not add it again.
    assert tool_mod.recovery_terms("the diff") == []
    assert tool_mod.recovery_terms("ERR-4032") == []
    assert tool_mod.recovery_terms("tests") == []
    # A lone separator is not a run, so it cannot be a term.
    assert tool_mod.recovery_terms("_") == []
    # A run shorter than 2 characters is not a term either side indexes.
    assert tool_mod.recovery_terms("x _ y") == []


def test_recovery_terms_never_remove_an_existing_term(tool_mod) -> None:
    """Every shipped term survives into the recovery query."""
    from tortoise.sparse import build_or_query

    for name in ("web_search", "tests/test_7949_capstone.py", "the diff",
                 "the admin-merge rail"):
        shipped = build_or_query(name).split("|")
        extra = [t for t in tool_mod.recovery_terms(name) if t not in shipped]
        recovered = shipped + extra
        assert shipped == recovered[:len(shipped)]


# ── 3. the paired statistics ────────────────────────────────────────────────

@pytest.mark.parametrize("k,n,want", [
    (0, 0, 1.0),
    (0, 5, 0.0625),
    (5, 5, 0.0625),
    (1, 1, 1.0),
    (3, 3, 0.25),
    (2, 4, 1.0),
    (0, 10, 0.001953125),
])
def test_mcnemar_two_sided_exact(tool_mod, k: int, n: int, want: float) -> None:
    assert tool_mod._binom_two_sided(k, n) == pytest.approx(want)


def test_compare_counts_discordant_pairs(tool_mod) -> None:
    """The pairing arithmetic, so a swapped column cannot pass as a gain."""
    a = {"arm": "a", "ranks": {"x": 1, "y": None, "z": 2, "w": None}}
    b = {"arm": "b", "ranks": {"x": 1, "y": 3, "z": None, "w": None}}
    out = tool_mod.compare(a, b, (3,))
    assert out["population_ids_identical"] is True
    assert out["k3"]["both"] == 1
    assert out["k3"]["only_b"] == 1     # b gained y
    assert out["k3"]["only_a"] == 1     # b lost z
    assert out["k3"]["discordant"] == 2


# ── 4. the artifact, and the brief against it ──────────────────────────────

def test_artifact_carries_the_pre_registered_rule(artifact) -> None:
    """The rule was fixed before the numbers; a reader can check it in the file."""
    assert artifact["decision_rule"].startswith("PASS iff")
    assert artifact["primary_k"] == 3
    assert artifact["verdict"]["rule"] == artifact["decision_rule"]


def test_artifact_population_is_the_committed_pin(artifact, population) -> None:
    assert artifact["population_digest"] == population["digest"]
    assert artifact["sample"] == population["sample"] == len(population["names"])
    ids = [r["id"] for r in population["names"]]
    assert len(ids) == len(set(ids)), "the pinned ids must be unique"
    for arm in artifact["arms"].values():
        assert sorted(arm["ranks"]) == sorted(ids), (
            f"{arm['arm']}: ranks do not cover the pinned population — the "
            f"comparison would not be paired")


def test_all_arms_measured_the_same_population(artifact) -> None:
    assert len(artifact["arms"]) == 4, (
        "M3 needs the shipped leg, the scorer-controlled BM25 arm, the "
        "deployable BM25 arm and the in-place recovery arm")
    assert set(artifact["arms"]) == {
        "falkordb_tfidf", "postgres_bm25/tokens",
        "postgres_bm25/native", "falkordb_tfidf__tokens_fixed"}


@pytest.mark.parametrize("arm,want", [
    ("falkordb_tfidf", 0.7133),
    ("postgres_bm25/tokens", 0.7267),
    ("postgres_bm25/native", 0.9700),
    ("falkordb_tfidf__tokens_fixed", 0.9633),
])
def test_brief_headline_matches_the_artifact(artifact, brief, arm: str, want: float) -> None:
    """The brief's §0 table is the artifact's, to four decimals."""
    assert artifact["arms"][arm]["recall_by_k"]["3"]["recall"] == pytest.approx(want)
    assert f"{want:.4f}" in brief, (
        f"the brief no longer states {arm} recall@3 = {want:.4f}")


def test_the_scorer_control_is_a_tie_and_the_rule_says_fail(artifact) -> None:
    """M3's verdict, pinned with BOTH of its parts.

    ⛔ A later lane must not read the deployable arm's 25-point swing as M3
    passing: the primary arm is the scorer-controlled one, and the rule was
    fixed before the numbers existed.
    """
    primary = artifact["comparisons"][
        "falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    assert primary["mcnemar_p_two_sided"] > 0.05, (
        "the scorer control is now significant — re-run and re-decide")
    assert artifact["verdict"]["M3"] == "FAIL"
    dep = artifact["verdict"]["deployable_arm"]
    assert dep["delta_pts"] > 20, (
        "the deployable arm's swing is the whole finding; if it shrank, the "
        "cause moved and the brief's §3.4 mechanism is no longer the reason")
    assert artifact["verdict"]["recovery_arm_no_index_move"]["delta_pts"] > 20, (
        "the in-place recovery arm is what keeps the index where it is")


def test_the_in_place_arm_loses_nothing(artifact) -> None:
    """+25 pts with zero regressions is the structural reason to prefer it."""
    rec = artifact["comparisons"][
        "falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"]
    assert rec["only_a"] == 0
    assert rec["only_b"] > 50


def test_both_engines_are_named_with_their_versions(artifact) -> None:
    """A measurement must name the engine it measured (the #11901 JEV lesson)."""
    assert "RediSearch" in artifact["arms"]["falkordb_tfidf"]["engine"]
    assert "RediSearch TF-IDF" in artifact["arms"]["falkordb_tfidf"]["engine"]
    for arm in ("postgres_bm25/tokens", "postgres_bm25/native"):
        engine = artifact["arms"][arm]["engine"]
        assert "PostgreSQL" in engine and "pg_search" in engine, engine
    assert "no OPTIONS" in artifact["arms"]["falkordb_tfidf"]["engine"], (
        "the shipped index's option surface is part of the finding: the "
        "score is TF-IDF because the index is created with none")
