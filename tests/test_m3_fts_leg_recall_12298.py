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
2. **The tables drift away from the artifact.** The brief's published tables are
   recomputed from the committed `artifact.json` and compared CELL BY CELL —
   §0, §3.1, §3.2, §3.3 and §3.5 below, plus §2's census and §3.4's identifier
   decomposition — and the changelog's recall@3 table and fixture-count sentence
   are compared too (⛔ NOT the whole changelog: its paired-p and Δ columns are
   not pinned — saying "the changelog is compared" was wider than the tests, so
   the claim is the narrow true one, #12349 review cycle 3). A
   re-run that moves a number therefore reds here instead of leaving prose that
   the committed artifact contradicts (#12298 review cycle 2, findings 1–3: an
   earlier revision compared FOUR values by whole-document substring and claimed
   in three places that every headline number was covered — a coverage claim the
   pin did not enforce is the silent-rot class this file exists to close).
   ⛔ The cell comparisons are backed by a recomputation from the arms' per-query
   `ranks`: `recall_by_k`, every paired `both`/`only_a`/`only_b`/`discordant` AND
   the McNemar p (a pure function of the discordant split) are re-derived from the
   ranks that produced them, so the summary cells and the prose cannot agree with
   each other while disagreeing with the run (#12349 review, P2). `ranks` is the
   only per-query record the artifacts carry, and two populations live in them —
   `tokens_controlled_subset` is computed over 174 rows, not 300 — so the subset's
   cells are recomputed from the ranks restricted to the NON-degenerate ids, which
   the artifact records (`arms["<b>"]["degenerate_ids"]`).

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
import inspect
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOC_DIR = REPO_ROOT / "docs" / "research" / "2026-11-13-12298-m3-fts-leg"
BRIEF = DOC_DIR / "research-brief.md"
ARTIFACT = DOC_DIR / "artifact.json"
POPULATION = DOC_DIR / "population.json"
TOOL = REPO_ROOT / "tools" / "m3_fts_leg_recall.py"
CHANGELOG = (REPO_ROOT / "docs" / "architecture"
             / "SEARCH-ARCHITECTURE-CHANGELOG.md")
SEARCH_ARCH = REPO_ROOT / "docs" / "architecture" / "SEARCH-ARCHITECTURE.md"


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


@pytest.fixture(scope="module")
def brief_text(brief: str) -> str:
    """The brief as text, for the claims that must be PRESENT rather than equal."""
    return brief


@pytest.fixture(scope="module")
def changelog() -> str:
    assert CHANGELOG.exists(), f"missing committed changelog {CHANGELOG}"
    return CHANGELOG.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def search_arch() -> str:
    assert SEARCH_ARCH.exists(), f"missing committed decision doc {SEARCH_ARCH}"
    return SEARCH_ARCH.read_text(encoding="utf-8")


# ── 0. reading the published tables, to the CELL ───────────────────────────
# The brief's tables are hand-written prose products, so a whole-document
# substring test is not a pin: `"0.7133" in brief` stays green when the §0 cell
# that STATES it is rewritten to 0.5000, because the number also appears four
# other times (measured on the earlier revision). These helpers compare cells.

def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _plain(cell: str) -> str:
    return cell.replace("**", "").replace("`", "").strip()


def _table(text: str, *header_needles: str) -> list[list[str]]:
    """Body rows of the FIRST pipe table whose header carries every needle."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        if not all(any(n in c for c in _cells(line)) for n in header_needles):
            continue
        rows = []
        for body in lines[i + 1:]:
            if not body.lstrip().startswith("|"):
                break
            cells = _cells(body)
            if all(set(c) <= set("-: ") for c in cells):
                continue          # the `|---|---|` rule
            rows.append(cells)
        return rows
    raise AssertionError(f"no table whose header carries {header_needles!r}")


def _num(cell: str) -> float:
    """The first number in a cell, with the brief's bold/backtick decoration off."""
    m = re.search(r"-?\d+(?:\.\d+)?", _plain(cell))
    assert m, f"no number in the cell {cell!r}"
    return float(m.group())


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
        # The OR string is a real multi-term query (not a fallback to the raw
        # name), and NONE of its terms is the term the index actually holds.
        assert len(emitted) > 1, (name, emitted)
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
    """The term list reproduces `build_or_query`, byte-exactly.

    A control that silently drifted would measure a tokenizer that is not the
    product's and look perfectly healthy — so the tool refuses at run time
    (`assert_terms_match`) and this pins the same property at test time.

    ⚠️ Reproducing `build_or_query` is NOT the same as handing Postgres terms:
    on the DEGENERATE branch `build_or_query` returns the escaped RAW string, so
    there is no term list to hand over and `arm_terms` sends the raw text — the
    declared break in the control, pinned by
    `test_the_degenerate_branch_is_declared_not_hidden`.
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
    """Every shipped term survives into the recovery query — on the HARNESS's own builder.

    ⛔ This asserts the production function, not a re-implementation of it. An
    earlier cut rebuilt ``shipped + extra`` here and then checked that the list
    had ``shipped`` as its prefix — a tautology about a local variable that could
    not fail, presented as the pin for the zero-regression claim (#12349 review,
    P2).
    """
    from tortoise.sparse import build_or_query

    for name in ("web_search", "tests/test_7949_capstone.py", "the diff",
                 "the admin-merge rail", "ERR-4032"):
        shipped = build_or_query(name).split("|")
        recovered = tool_mod.recovery_query(name).split("|")
        assert set(shipped) <= set(recovered), (
            f"the recovery arm DROPPED a shipped term for {name!r}: "
            f"{sorted(set(shipped) - set(recovered))}")
        # It is additive and nothing else: the difference is exactly the runs the
        # shipped tokenizer split, and nothing not already in `recovery_terms`.
        assert set(recovered) - set(shipped) <= set(tool_mod.recovery_terms(name)), name
        if not tool_mod.recovery_terms(name):
            assert recovered == shipped, (
                f"nothing to add for {name!r}, so the query must be unchanged")


def test_the_artifact_ranks_reproduce_the_recall_table(artifact) -> None:
    """``recall_by_k`` must be re-derivable from the per-query ``ranks``.

    ⛔ The discriminating half of the brief pin (#12349 review, P2). The headline
    test compares each arm's ``recall`` cell to a literal and checks the literal
    appears in the brief — but the same literals appear in several cells, so a
    swapped or hand-edited cell would pass. This recomputes every k for every arm
    from the ranks the run recorded, which no edit to a summary cell can satisfy.
    """
    for arm_name, arm in artifact["arms"].items():
        ranks = arm["ranks"]
        n = len(ranks)
        assert n == artifact["sample"], (arm_name, n)
        for k, cell in arm["recall_by_k"].items():
            found = [r for r in ranks.values() if r is not None and r <= int(k)]
            assert cell["in_window"] == len(found), (arm_name, k)
            assert cell["recall"] == pytest.approx(round(len(found) / n, 4)), (
                arm_name, k, cell["recall"], round(len(found) / n, 4))
            assert cell["rows_zero"] <= n, (arm_name, k)


def test_the_paired_comparison_recomputes_from_both_arms_ranks(
        artifact, tool_mod) -> None:
    """Every paired statistic must fall out of the two arms' ranks.

    Same reasoning as the test above: ``delta_pts``/``only_a``/``only_b`` are one
    subtraction away from the ranks, so recomputing them is what makes the
    published ``+25.00 pts`` a measurement rather than a claimed cell.

    ⛔ Two populations live in this artifact and they are NOT
    interchangeable (#12349 review, P2 caught the first cut of this test doing
    exactly that): ``tokens_controlled_subset`` is computed over the queries with
    a surviving token list (its recorded ``n``, with ``excluded_degenerate``
    removed), so its cells cannot be checked against the FULL arms' ranks. They are
    recomputed from the ranks restricted to the NON-degenerate ids, which the
    artifact records (``arms["<b>"]["degenerate_ids"]``); that is the same
    computation as the full comparisons, on the population the comparison claims.
    ⛔ An earlier revision of this docstring described the weaker version it
    replaced — "checked for internal consistency, against its own ``n``" — which
    is a self-consistency check a self-consistent rewrite passes (review cycle 4,
    finding 5). The code below had already been made real; the docstring had not.
    """
    for key, cmp in artifact["comparisons"].items():
        ra = artifact["arms"][cmp["a"]]["ranks"]
        rb = artifact["arms"][cmp["b"]]["ranks"]
        n_full = len(ra)
        # The subset comparisons drop the degenerate queries; anything else that
        # disagrees with the sample size is a broken pairing, not a subset.
        subset = "n" in cmp
        n = cmp["n"] if subset else n_full
        if not subset:
            assert sorted(ra) == sorted(rb), key
            assert n == artifact["sample"], (key, n)
        for k in artifact["ks"]:
            cell = cmp[f"k{k}"]
            # Invariants that hold for ANY population: the cells agree with each
            # other, and the delta is the one division away from them.
            a_found = cell["only_a"] + cell["both"]
            b_found = cell["only_b"] + cell["both"]
            assert cell["discordant"] == cell["only_a"] + cell["only_b"], (key, k)
            assert cell["delta_pts"] == pytest.approx(
                round(100.0 * (b_found - a_found) / n, 2)), (key, k)
            assert cell["a_recall"] == pytest.approx(round(a_found / n, 4)), (key, k)
            assert cell["b_recall"] == pytest.approx(round(b_found / n, 4)), (key, k)
            # ⛔ The p value is a pure function of the discordant split, so it is
            # recomputed here rather than trusted: a hand-edited significance is
            # the single cell a reader will quote to settle an argument.
            assert cell["mcnemar_p_two_sided"] == pytest.approx(
                round(tool_mod._binom_two_sided(
                    cell["only_b"], cell["discordant"]), 5)), (key, k)
            # The discriminating half: the cells recomputed straight from the
            # ranks. For the SUBSET comparison the population is the non-degenerate
            # one, and the artifact records WHICH ids are degenerate
            # (`arms["<b>"]["degenerate_ids"]`), so this is done from the ranks
            # there too instead of being skipped (#12349 review cycle 2,
            # finding 4: a self-consistent rewrite of the subset's cells passed).
            a_ids = {i for i, r in ra.items() if r is not None and r <= k}
            b_ids = {i for i, r in rb.items() if r is not None and r <= k}
            if subset:
                deg = set(artifact["arms"][cmp["b"]]["degenerate_ids"])
                a_ids -= deg
                b_ids -= deg
                assert deg <= set(ra), (key, len(deg - set(ra)))
                assert len(ra) - len(deg) == cmp["n"], (key, cmp["n"])
            assert cell["only_a"] == len(a_ids - b_ids), (key, k)
            assert cell["only_b"] == len(b_ids - a_ids), (key, k)
            assert cell["both"] == len(a_ids & b_ids), (key, k)
            assert cell["discordant"] == len(a_ids ^ b_ids), (key, k)
        if subset:
            assert cmp["excluded_degenerate"] + n == n_full, (
                key, cmp["excluded_degenerate"], n, n_full)


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

def test_artifact_carries_the_rule_as_the_harness_constant(
        tool_mod, artifact, brief_text) -> None:
    """The rule is a CONSTANT in the harness and travels verbatim into the artifact.

    ⚠️ What this proves: the rule cannot be re-chosen per run — the artifact's
    `decision_rule` is byte-identical to `DECISION_RULE` in the harness, so a
    different rule has to be edited in two places, and the brief quotes the same
    operative clauses.

    ⛔ What it does NOT prove, and the earlier cut of this test implied it did:
    that the constant existed BEFORE the 300-name run. Harness, artifact and
    brief landed in ONE commit, so the ordering is a claim about the lane's
    process, not a fact this repository can check — the repo's own executable
    form of that claim (`tools/capstone_preregistration_check.py`) needs an
    evidence log that predates the registration, which this lane does not have.
    """
    assert artifact["decision_rule"] == tool_mod.DECISION_RULE
    assert artifact["decision_rule"].startswith("PASS iff")
    assert artifact["primary_k"] == 3
    assert artifact["verdict"]["rule"] == artifact["decision_rule"]
    for clause in ("strictly greater", "exact two-sided McNemar", "p < 0.05"):
        assert clause in artifact["decision_rule"], clause
        assert clause in brief_text, (
            f"the brief no longer quotes the rule's own clause {clause!r}")


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
    ("postgres_bm25/native", 0.9633),
    ("falkordb_tfidf__tokens_fixed", 0.9633),
])
def test_brief_headline_matches_the_artifact(artifact, brief, arm: str, want: float) -> None:
    """The brief's §0 table is the artifact's, ROW BY ROW.

    ⛔ Bound to the row, not to the document: `f"{want:.4f}" in brief` passed
    while the §0 cell itself said something else, because the same four decimals
    appear elsewhere in the brief (measured — the previous revision had exactly
    that hole; review cycle 2, finding 2).
    """
    assert artifact["arms"][arm]["recall_by_k"]["3"]["recall"] == pytest.approx(want)
    got = {}
    for cells in _table(brief, "recall@3", "Δ vs shipped"):
        m = re.match(r"`([^`]+)`", cells[0])
        if m:
            got[m.group(1)] = cells[1]
    assert arm in got, f"the brief's §0 table has no `{arm}` row"
    assert round(_num(got[arm]), 4) == round(want, 4), (
        f"the brief's §0 row for {arm} states {_plain(got[arm])!r}; the committed "
        f"artifact says {want:.4f} — rewrite the brief, do not relax the pin")


def test_the_scorer_control_is_a_tie_and_the_rule_says_fail(artifact, brief_text) -> None:
    """M3's verdict, pinned with BOTH of its parts.

    ⛔ A later lane must not read the deployable arm's 25-point swing as M3
    passing: the primary arm is the scorer-controlled one, and the rule is the
    harness's `DECISION_RULE` constant, asserted byte-identical in
    `test_artifact_carries_the_rule_as_the_harness_constant`.
    """
    primary = artifact["comparisons"][
        "falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    assert primary["mcnemar_p_two_sided"] > 0.05, (
        "the scorer control is now significant — re-run and re-decide")
    assert artifact["verdict"]["M3"] == "FAIL"
    # ⛔ The closing condition in the issue's OWN falsifier sentence is NOT
    # triggered — a deployable BM25-in-Postgres beats the current scorer —
    # which is why M3's own rule can still say FAIL here. The brief must
    # present BOTH readings; this pin makes the second one impossible to
    # drop when the first is quoted. ⛔ Do NOT write "the falsifier is
    # satisfied": quote-mined, that says the leg is CLOSED, the opposite of
    # what this assertion exists for.
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    assert subset["n"] < artifact["sample"], (
        "the tokens control is only real on the non-degenerate queries; the "
        "artifact must record the subset it holds on")
    assert subset["mcnemar_p_two_sided"] > 0.05
    assert "Reading 1" in brief_text and "Reading 2" in brief_text, (
        "the brief must state the literal-falsifier reading as well as the "
        "decomposition — M3 is not simply 'FAIL'")
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


# ── 5. the declared break in the control, and the DoD's own requirements ────

def test_the_degenerate_branch_is_declared_not_hidden(tool_mod, artifact) -> None:
    """126 of 300 queries have no term list to control — and the artifact says so.

    On the degenerate branch `build_or_query` returns the escaped RAW string, so
    `arm_terms` sends the raw text and the two Postgres arms are ONE arm. That
    was a P1 review finding on the first version of this harness, which claimed
    a constant tokenizer everywhere; the census is now recorded as data.
    """
    census = artifact["terms_census"]
    assert census["queries"] == artifact["sample"]
    assert 0 < census["degenerate"] < census["queries"]
    assert census["degenerate"] + census["tokens_controlled"] == census["queries"]
    # the arm agrees with the census
    assert artifact["arms"]["postgres_bm25/tokens"]["degenerate_n"] == census["degenerate"]

    # What the two arms do on a degenerate query, stated as behaviour.
    assert tool_mod.arm_terms("the diff") == ("the diff", True)
    assert tool_mod.arm_terms("web_search") == ("search web", False)


def test_degenerate_rows_are_identical_across_the_two_postgres_arms(artifact) -> None:
    """The mechanism of the break, pinned: on those rows the arms coincide."""
    deg = artifact["arms"]["postgres_bm25/tokens"]["degenerate_ids"]
    tok = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    nat = artifact["arms"]["postgres_bm25/native"]["ranks"]
    differing = [i for i in deg if tok[i] != nat[i]]
    assert not differing, (
        f"{len(differing)} degenerate rows differ between the arms — the "
        f"declared break is no longer what the artifact says it is")


def test_the_tie_rule_is_matched(tool_mod, artifact, brief_text) -> None:
    """Requirement 6: the id tie key is DESC on both stores — pinned at the SQL.

    ⚠️ The artifact's `engine` note is PROSE beside the SQL, not the SQL itself,
    so the id key is asserted against the harness SOURCE as well; otherwise a
    query change could diverge from the note with this test green.
    """
    src = inspect.getsource(tool_mod)
    assert "ORDER BY s DESC, id DESC LIMIT %s" in src, (
        "the Postgres arm's ORDER BY moved — re-check the tie rule before "
        "trusting the artifact's engine note")
    assert "ORDER BY paradedb.score(id) DESC, " in src
    for arm in ("postgres_bm25/tokens", "postgres_bm25/native"):
        assert "id DESC" in artifact["arms"][arm]["engine"], (
            f"{arm} does not state the tie key it ordered by")
    assert "DESC on every label" in artifact["verdict"]["requirements"]["6_tie_rule"]

    # ⚠️ The RECENCY half is not controlled, and its mechanism is checkable: the
    # projection stamps `createdAt` ON CREATE, so the shipped leg's recency key
    # is live while the Postgres arm has no timestamp — the two orders agree only
    # because the sweep writes in ascending id order.
    entities = (REPO_ROOT / "tortoise" / "projection" / "entities.py").read_text(
        encoding="utf-8")
    assert "o.createdAt=coalesce($ca, $now)" in entities, (
        "the :Object upsert no longer stamps createdAt on create — the brief's "
        "declared residual for the recency half is now wrong")
    assert "ascending id order" in brief_text


def test_the_scorers_do_not_rank_identically(artifact, brief_text) -> None:
    """§0's zero-discordance result is about the WINDOW, not the order.

    ⛔ The first cut of the brief read "zero discordant pairs at k=15" as "the
    two scorers rank these documents identically", and the artifact refutes it:
    a name can sit inside the window on both arms and still move five places.
    """
    shipped = artifact["arms"]["falkordb_tfidf"]["ranks"]
    tokens = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    both = [q for q in shipped if shipped[q] is not None and tokens[q] is not None]
    moved = [q for q in both if shipped[q] != tokens[q]]
    assert len(both) == 228, len(both)
    assert len(moved) == 45, (len(moved), sorted(moved)[:5])
    assert max(abs(shipped[q] - tokens[q]) for q in moved) == 5
    assert (shipped["m3-000014"], tokens["m3-000014"]) == (1, 6)
    assert artifact["arms"]["falkordb_tfidf"]["recall_by_k"]["15"][
        "mean_rank_when_found"] > artifact["arms"]["postgres_bm25/tokens"][
        "recall_by_k"]["15"]["mean_rank_when_found"]
    # and the brief must state it — the false reading must not come back.
    assert "rank these documents identically" not in brief_text
    assert "45 rank differently" in brief_text
    assert "1.452 vs 1.285" in brief_text


def test_the_threshold_arm_is_recorded_as_not_exercised(artifact) -> None:
    """Requirement 4: the single-leg floor arm must be stated, not implied."""
    thr = artifact["threshold_arm"]
    assert thr["applied"] is False
    assert thr["shipped_default"] == 0.0
    assert "not exercised" in artifact["verdict"]["requirements"]["4_single_leg_floor_arm"]


FIXTURES_EXPECTED = {
    "m3fx-0": "ERR-4032",
    "m3fx-1": "INV-2024-0091",
    "m3fx-2": "abc_def",
    "m3fx-3": "test_7949_capstone_verdict_check",
    "m3fx-4": "ERR-4032 retry",
}


def test_the_identifier_fixture_runs_on_every_arm(artifact) -> None:
    """Requirement 5 is the DoD's correctness gate — and it is on every arm."""
    for name, arm in artifact["arms"].items():
        fx = arm.get("identifier_fixture")
        assert fx is not None, f"{name} carries no identifier fixture"
        assert {r["id"] for r in fx["results"]} == set(FIXTURES_EXPECTED)
        for r in fx["results"]:
            assert r["stored"] == FIXTURES_EXPECTED[r["id"]]
            assert "terms_sent" in r and "rank" in r and "rank_wide" in r
        assert fx["PASS"] == all(r["rank_wide"] is not None
                                 for r in fx["results"])


def test_the_shipped_leg_fails_the_identifier_fixture_and_the_arms_differ(artifact) -> None:
    """The finding the fixture exists to surface.

    ⛔ If the shipped leg starts PASSING this, the tokenizer defect is fixed:
    re-measure M3 and rewrite the brief §3.5 rather than relaxing this.
    """
    shipped = artifact["arms"]["falkordb_tfidf"]["identifier_fixture"]
    fixed = artifact["arms"]["falkordb_tfidf__tokens_fixed"]["identifier_fixture"]
    assert shipped["PASS"] is False, (
        "the shipped leg now passes the #8933 fixture — the defect is repaired; "
        "re-measure")
    assert fixed["PASS"] is True
    bad = {r["stored"] for r in shipped["results"] if r["rank_wide"] is None}
    assert "abc_def" in bad, (
        "abc_def being unretrievable by its own name (no row at the production "
        "window AND none at k=15 — that is what the artifact records; it is not a "
        "row COUNT) is the crispest case in "
        "the lane; if it stopped happening the mechanism moved")
    # The scorer is not what fixes it: our terms fail, the analyser passes.
    assert artifact["arms"]["postgres_bm25/tokens"]["identifier_fixture"]["PASS"] is False
    assert artifact["arms"]["postgres_bm25/native"]["identifier_fixture"]["PASS"] is True


def test_a_second_independent_run_reproduced_every_table() -> None:
    """`artifact-repeat.json` is the repeatability evidence, and it must agree.

    An earlier draft claimed a repeat in prose while committing one artifact; a
    number a reader cannot check does not belong in the same table as one they
    can.
    """
    rep = DOC_DIR / "artifact-repeat.json"
    assert rep.exists(), "the repeatability artifact is missing"
    a = json.loads(ARTIFACT.read_text())
    b = json.loads(rep.read_text())
    assert a["population_digest"] == b["population_digest"]
    for name in a["arms"]:
        assert a["arms"][name]["recall_by_k"] == b["arms"][name]["recall_by_k"], name
        assert a["arms"][name]["per_class"] == b["arms"][name]["per_class"], name
    assert a["verdict"] == b["verdict"]
    assert a["falkordb_graph"] != b["falkordb_graph"], (
        "each run must use its own graph — a reused graph re-upserts into a "
        "live index")


def test_the_fixture_ordering_defect_is_recorded_in_both_artifacts() -> None:
    """The 305-vs-300 disclosure must exist, and be true of what the artifacts hold.

    ⛔ This pins a DISCLOSURE, which is the one thing the rest of this file does
    not do for the harness's own defect (#12349 review cycle 2, finding 8). The
    brief states the caveat and points a reader at `measurement_notes`; with no
    assertion behind it, an artifact edit — or a re-run — leaves the prose
    claiming a defect the record no longer shows, which is the same rot this file
    exists to stop for every published NUMBER.

    The relationship, not the literal: both FalkorDB arms share one population, so
    the recovery arm's index must carry exactly the five `#8933` fixture rows the
    shipped arm inserted before it. ⛔ If the re-run that the harness fix makes
    possible lands, BOTH counts become the population size and this reds — that is
    intended: whoever re-runs must replace the caveat in the brief and this pin in
    the same change, rather than leaving a disclosure that describes a run nobody
    can reproduce.
    """
    fixture_rows = 5
    for name in ("artifact.json", "artifact-repeat.json"):
        path = DOC_DIR / name
        assert path.exists(), f"missing committed artifact {path}"
        doc = json.loads(path.read_text())
        notes = doc.get("measurement_notes")
        assert notes, f"{name} must carry `measurement_notes` — the brief cites it"
        assert "FIXTURE ORDERING" in notes, (name, "the note must name the defect")
        shipped = doc["arms"]["falkordb_tfidf"]["index_numDocuments"]
        recovery = doc["arms"]["falkordb_tfidf__tokens_fixed"]["index_numDocuments"]
        assert shipped == doc["sample"], (name, shipped, doc["sample"])
        assert recovery == shipped + fixture_rows, (
            f"{name}: the recovery arm's index is {recovery} against the shipped "
            f"arm's {shipped} — the fixture-ordering defect has changed shape, so "
            "the brief's caveat and this pin must be re-derived together")


def test_an_authoring_path_did_not_leak_into_the_artifacts() -> None:
    """`population_file` must be repo-relative — an absolute path is one box's fact."""
    for name in ("artifact.json", "artifact-repeat.json"):
        doc = json.loads((DOC_DIR / name).read_text())
        rel = doc["population_file"]
        assert not rel.startswith("/"), (
            f"{name}: `population_file` is an absolute authoring path ({rel})")
        assert (REPO_ROOT / rel).exists(), (name, rel)


# ── 5. every published table is the artifact's, CELL BY CELL ────────────────
# Review cycle 2, finding 1: the pins above compare the ARTIFACT to itself. The
# brief's published tables — the per-class decomposition §4.3's argument rests
# on, the paired tests, the k=5/10/15 rows — were compared to nothing, so a
# re-run that regenerated `artifact.json` left every one of them quotable and
# false while `--integrity` and the `core`/`tier1` registrations stayed green.
# The section below binds each published cell to the committed artifact.

ARM_ORDER_31 = (
    "falkordb_tfidf", "postgres_bm25/tokens", "postgres_bm25/native",
    "falkordb_tfidf__tokens_fixed",
)


def test_brief_rank_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.1 — the whole recall@k table, the empty-row census and the latencies."""
    rows = {_plain(r[0]).lower(): r for r in _table(brief, "falkordb__tokens_fixed")}
    assert len(rows) == 6, f"§3.1 no longer has its 6 rows: {sorted(rows)}"
    for k in ("3", "5", "10", "15"):
        cells = rows[k]
        for arm, cell in zip(ARM_ORDER_31, cells[1:], strict=True):
            want = artifact["arms"][arm]["recall_by_k"][k]["recall"]
            assert round(_num(cell), 4) == round(want, 4), (
                f"§3.1 row k={k}, arm {arm}: brief {_plain(cell)!r} vs artifact "
                f"{want:.4f}")
    zero_row = rows["empty result sets"]
    for arm, cell in zip(ARM_ORDER_31, zero_row[1:], strict=True):
        want = artifact["arms"][arm]["recall_by_k"]["3"]["rows_zero"]
        assert int(_num(cell)) == want, (
            f"§3.1 empty-result-sets row, arm {arm}: brief {_plain(cell)!r} vs "
            f"artifact {want}")
    ms_row = rows["ms per query (cpu, this box)"]
    for arm, cell in zip(ARM_ORDER_31, ms_row[1:], strict=True):
        want = artifact["arms"][arm]["ms_per_query"]
        assert round(_num(cell), 2) == round(want, 2), (
            f"§3.1 ms-per-query row, arm {arm}: brief {_plain(cell)!r} vs "
            f"artifact {want}")


def test_brief_paired_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.2 — both / shipped only / other only / Δ pts / p, for every comparison."""
    cmp = artifact["comparisons"]
    want = {
        ("pg/tokens", 3): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"],
        ("subset", 3): cmp["tokens_controlled_subset"]["k3"],
        ("pg/tokens", 15): cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"],
        ("pg/native", 3): cmp["falkordb_tfidf__vs__postgres_bm25/native"]["k3"],
        ("in-place fix", 3): cmp["falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"],
    }
    seen = set()
    for cells in _table(brief, "shipped only", "other only"):
        label = _plain(cells[0]).lower()
        k = int(_num(cells[1]))
        if "subset" in label:
            key = ("subset", k)
        elif "in-place" in label:
            key = ("in-place fix", k)
        elif "native" in label:
            key = ("pg/native", k)
        elif "pg/tokens" in label or "tokens" in label:
            key = ("pg/tokens", k)
        else:                                    # pragma: no cover - a new row
            raise AssertionError(f"§3.2 row this pin cannot identify: {cells[0]!r}")
        assert key in want, f"§3.2 row {key} is not one the artifact records"
        assert key not in seen, f"§3.2 states {key} twice"
        seen.add(key)
        pair = want[key]
        for cell, field, nd in (
            (cells[2], "both", 0),
            (cells[3], "only_a", 0),
            (cells[4], "only_b", 0),
            (cells[5], "delta_pts", 2),
        ):
            assert round(_num(cell), nd) == round(pair[field], nd), (
                f"§3.2 {key} {field}: brief {_plain(cell)!r} vs artifact {pair[field]}")
        p_cell = _plain(cells[6])
        if p_cell.startswith("<"):
            assert pair["mcnemar_p_two_sided"] < _num(p_cell), (
                f"§3.2 {key} p: brief {p_cell!r} vs artifact "
                f"{pair['mcnemar_p_two_sided']}")
        else:
            assert round(_num(p_cell), 3) == round(pair["mcnemar_p_two_sided"], 3), (
                f"§3.2 {key} p: brief {p_cell!r} vs artifact "
                f"{pair['mcnemar_p_two_sided']}")
    assert seen == set(want), f"§3.2 lost a comparison: {sorted(set(want) - seen)}"


def test_brief_per_class_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.3 — the per-class recall@3 / recall@15 grid, which §4.3 argues from."""
    cls_of = {"definite": "definite", "identifier": "ident", "path": "path"}
    seen = set()
    # The header is `| class | n | shipped | pg/tokens | pg/native | in-place fix |`;
    # the `(recall@3 / recall@15)` in the section heading is prose, not a cell.
    for cells in _table(brief, "class", "in-place fix"):
        label = _plain(cells[0]).lower()
        cls = next((v for k, v in cls_of.items() if label.startswith(k)), None)
        assert cls, f"§3.3 class row this pin cannot identify: {cells[0]!r}"
        seen.add(cls)
        n = int(_num(cells[1]))
        assert n == artifact["arms"]["falkordb_tfidf"]["per_class"][cls]["n"], (
            f"§3.3 {cls} n: brief {n} vs artifact "
            f"{artifact['arms']['falkordb_tfidf']['per_class'][cls]['n']}")
        for arm, cell in zip(ARM_ORDER_31, cells[2:], strict=True):
            k3, k15 = (round(_num(p), 4) for p in _plain(cell).split("/"))
            for k, got in (("3", k3), ("15", k15)):
                want = artifact["arms"][arm]["per_class"][cls][k]
                assert got == round(want, 4), (
                    f"§3.3 {cls} arm {arm} recall@{k}: brief {got} vs artifact {want}")
    assert seen == set(cls_of.values()), f"§3.3 lost a class: {sorted(set(cls_of.values()) - seen)}"


def test_brief_fixture_table_is_the_artifact(artifact, brief: str) -> None:
    """§3.5 — the `#8933` fixture table and the sentence that summarises it."""
    arms = ("falkordb_tfidf", "falkordb_tfidf__tokens_fixed",
            "postgres_bm25/tokens", "postgres_bm25/native")
    by_stored = {
        arm: {r["stored"]: r for r in artifact["arms"][arm]["identifier_fixture"]["results"]}
        for arm in arms
    }
    rows = _table(brief, "stored name", "query")
    assert len(rows) == 5, f"§3.5 no longer has its five fixture rows: {len(rows)}"
    for cells in rows:
        stored = _plain(cells[0])
        for arm, cell in zip(arms, cells[2:], strict=True):
            res = by_stored[arm][stored]
            got = _plain(cell).split(" (")[0].strip()
            if got == "never":
                assert res["rank_wide"] is None, (
                    f"§3.5 {stored} on {arm}: the brief says never, the artifact "
                    f"reaches it at k=15 rank {res['rank_wide']}")
            else:
                expect = res["rank"] if res["rank"] is not None else res["rank_wide"]
                assert int(_num(got)) == expect, (
                    f"§3.5 {stored} on {arm}: brief {got!r} vs artifact rank {expect}")
    # The summary sentence, built from the artifact rather than restated.
    fx_arm = "falkordb_tfidf__tokens_fixed"
    sh = artifact["arms"]["falkordb_tfidf"]["identifier_fixture"]
    fx = artifact["arms"][fx_arm]["identifier_fixture"]
    tk = artifact["arms"]["postgres_bm25/tokens"]["identifier_fixture"]
    nv = artifact["arms"]["postgres_bm25/native"]["identifier_fixture"]
    n = sh["n"]
    sentence = (f"Shipped: {sh['reachable']} of {n} reachable. In-place fix: "
                f"{fx['reachable']} of {n}. `pg/tokens`: {tk['reachable']} of {n}. "
                f"`pg/native`: {nv['reachable']} of {n}.")
    assert sentence in " ".join(brief.split()), (
        f"§3.5's summary sentence must read {sentence!r} — the artifact says the "
        f"shipped leg reaches {sh['reachable']} of {n} and the in-place fix "
        f"{fx['reachable']} of {n}")


def test_brief_census_and_identifier_decomposition_are_the_artifact(
        artifact, population, brief: str) -> None:
    """§2's control break and §3.4's identifier decomposition — recomputed.

    The decomposition is what makes §4.3's conclusion ("the scorer is not the
    constraint") checkable, and it is arithmetic over the committed ranks, so a
    re-run must move it here rather than only in the artifact.
    """
    text = " ".join(brief.split())
    census = artifact["terms_census"]
    assert (f"{census['degenerate']} of the {census['queries']} queries are "
            "degenerate") in text, (
        "§2's degenerate-query census no longer matches `terms_census` — the "
        "control break is stated as 126 of 300 and recorded as "
        f"{census['degenerate']} of {census['queries']}")
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    assert f"**{subset['n']} tokens-controlled queries**" in text or \
        f"the **{subset['n']} tokens-controlled queries**" in text, (
            f"§2 must name the {subset['n']}-query tokens-controlled population")
    # §0's subset row states BOTH recalls, and §0 is where a reader quotes from.
    subset_row = next(
        (r for r in _table(brief, "recall@3", "Δ vs shipped")
         if "subset" in _plain(r[0]).lower()), None)
    assert subset_row is not None, "§0 no longer carries the subset row"
    base, arm = (round(_num(p), 4) for p in _plain(subset_row[1]).split("vs"))
    # The brief states this cell arm-vs-baseline (`0.5287 vs 0.5172`), the same
    # order §3.2's Δ column uses; read it that way rather than assuming it.
    assert arm == round(subset["recall_at_3_base"], 4), (
        f"§0 subset baseline: brief {arm} vs artifact {subset['recall_at_3_base']}")
    assert base == round(subset["recall_at_3"], 4), (
        f"§0 subset arm: brief {base} vs artifact {subset['recall_at_3']}")

    ident = [n for n in population["names"] if n["cls"] == "ident"]
    hashed = [n["id"] for n in ident if "#" in n["name"]]
    snake = [n["id"] for n in ident if "#" not in n["name"]]

    def found(arm_name: str, ids: list[str]) -> int:
        ranks = artifact["arms"][arm_name]["ranks"]
        # ⛔ `<= 3`, not `< 3` — rank 3 IS found at k=3, and `< 3` silently
        # recomputes recall@2 while the brief publishes the result as recall@3
        # (review r3: inert in the committed artifact only because no `ident`
        # name sits at exactly rank 3, which is a property of a FROZEN file).
        # `ranks` is 1-BASED — `ids.index(row["id"]) + 1` in the harness — which
        # is what makes the off-by-one invisible to a reader comparing it against
        # a 0-based mental model of `index()`.
        return sum(1 for i in ids if ranks.get(i) is not None and ranks[i] <= 3)

    sh = found("falkordb_tfidf", hashed)
    sn_sh = found("falkordb_tfidf", snake)
    sn_fx = found("falkordb_tfidf__tokens_fixed", snake)
    assert (f"the {len(hashed)} `#n` shapes") in text
    assert f"**{sh} of {len(hashed)}**" in text, (
        f"§3.4 must state {sh} of {len(hashed)} for the `#n` shapes")
    plain = text.replace("**", "")
    want = (f"the {len(snake)} `snake_case` names: {sn_sh} of {len(snake)} "
            f"({sn_sh / len(snake):.3f}) shipped → {sn_fx} of {len(snake)} "
            f"({sn_fx / len(snake):.3f}) with the in-place fix, "
            f"+{(sn_fx - sn_sh) / len(snake) * 100:.1f} pts")
    assert want in plain, (
        "§3.4's identifier decomposition must re-derive from the committed ranks; "
        f"expected {want!r}")


def test_changelog_does_not_restate_superseded_numbers(artifact, changelog: str) -> None:
    """`SEARCH-ARCHITECTURE-CHANGELOG.md` duplicates §0 and the fixture counts.

    It is the record `SEARCH-ARCHITECTURE.md` §6.2's reader opens to learn why the
    text index did not move, and nothing read it before this test: a re-run could
    move every number in the brief and leave the changelog quoting the superseded
    ones forever (review cycle 2, finding 3).
    """
    arms = artifact["arms"]
    rows = _table(changelog, "Δ vs shipped", "recall@3")
    expected = [
        ("db.idx.fulltext", arms["falkordb_tfidf"]["recall_by_k"]["3"]["recall"]),
        ("our production terms", arms["postgres_bm25/tokens"]["recall_by_k"]["3"]["recall"]),
        ("its own analyser", arms["postgres_bm25/native"]["recall_by_k"]["3"]["recall"]),
        ("the `_`-preserving runs",
         arms["falkordb_tfidf__tokens_fixed"]["recall_by_k"]["3"]["recall"]),
    ]
    for needle, want in expected:
        hit = [r for r in rows if needle in r[0]]
        assert len(hit) == 1, f"the changelog table lost its {needle!r} row"
        assert round(_num(hit[0][1]), 4) == round(want, 4), (
            f"the changelog states recall@3 {_plain(hit[0][1])} for {needle!r}; "
            f"the committed artifact says {want:.4f}")
    subset = [r for r in rows if "subset" in r[0]]
    assert len(subset) == 1, "the changelog table lost its tokens-controlled subset row"
    pair = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    # Arm-vs-baseline, the same order the brief's §0 cell uses.
    arm, base = (round(_num(p), 4) for p in _plain(subset[0][1]).split("vs"))
    assert (arm, base) == (round(pair["recall_at_3"], 4),
                           round(pair["recall_at_3_base"], 4))

    text = " ".join(changelog.replace("**", "").split())
    n = arms["falkordb_tfidf"]["identifier_fixture"]["n"]
    reach = {a: arms[a]["identifier_fixture"]["reachable"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed",
        "postgres_bm25/tokens", "postgres_bm25/native")}
    sentence = (f"shipped {reach['falkordb_tfidf']} of {n} reachable, the in-place "
                f"query {reach['falkordb_tfidf__tokens_fixed']} of {n}, BM25 fed our "
                f"terms {reach['postgres_bm25/tokens']} of {n}, BM25 with its own "
                f"analyser {reach['postgres_bm25/native']} of {n}.")
    assert sentence in text, (
        "the changelog's `#8933` fixture counts must be the artifact's; expected "
        f"{sentence!r}")


# ── 6. the two things a re-run would otherwise decide wrongly ───────────────

def test_the_degraded_leg_refusal_is_WIRED_at_every_shipped_leg_call(tool_mod) -> None:
    """The refusal must be called AT the call site, with the trace actually passed.

    ⛔ A unit test of `_refuse_degraded_legs` alone cannot see the two regressions
    that matter (#12349 review cycle 5, finding 3 — both measured GREEN before
    this test existed): deleting the call from the sweep, and calling
    `_refuse_degraded_legs(trace, q)` while dropping `leg_trace=trace` from
    `run_fts_query`. The second leaves the trace EMPTY, so the refusal is a
    silent no-op — which is the fail-open it was written to close. Neither needs
    a database to detect: they are properties of the source.

    So this parses the harness and asserts the wiring: every non-warmup
    `run_fts_query(...)` call in `arm_falkordb` passes `leg_trace=` a NAMED list,
    and each `_refuse_degraded_legs(...)` call is handed the same name the
    nearest preceding measured call wrote its trace to. A count would not do:
    refusing on a DIFFERENT list leaves the refusal reading an empty trace and
    passing every degraded read.
    """
    import ast

    src = TOOL.read_text()
    tree = ast.parse(src)
    sweep = next((n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "arm_falkordb"), None)
    assert sweep is not None, "arm_falkordb is the measurement's sweep"

    def _calls(node, name):
        return [n for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and (getattr(n.func, "id", None) == name
                     or getattr(n.func, "attr", None) == name)]

    def _is_warmup(call) -> bool:
        """The pre-wait warm query, whose RESULT is discarded.

        ⛔ Excluded deliberately, and the exclusion is the only one: it runs
        BEFORE `_wait_indexed`, so on a cold start the index legitimately is not
        built yet and a refusal there would abort every honest run. It measures
        nothing, so it cannot publish a degraded read as a number.
        """
        return any(isinstance(a, ast.Constant) and a.value == "warm"
                   for a in call.args)

    legs = [c for c in _calls(sweep, "run_fts_query") if not _is_warmup(c)]
    assert legs, "the shipped leg must be called from the sweep"
    refusals = _calls(sweep, "_refuse_degraded_legs")
    assert len(refusals) >= len(legs), (
        f"{len(legs)} `run_fts_query` call(s) but only {len(refusals)} "
        "`_refuse_degraded_legs` call(s) — an unguarded shipped-leg call books a "
        "degraded read as a measurement")
    traces = {}
    for call in legs:
        kw = {k.arg: k.value for k in call.keywords}
        assert "leg_trace" in kw, (
            "a `run_fts_query` call does not pass `leg_trace=` — the trace stays "
            "empty and `_refuse_degraded_legs` becomes a silent no-op")
        assert isinstance(kw["leg_trace"], ast.Name), (
            "`leg_trace=` must be a named list the refusal can also read, not an "
            "inline expression")
        traces[call.lineno] = kw["leg_trace"].id
    # ⛔ Identity, not a count (#12349 review cycle 5/6): passing `leg_trace=trace`
    # and then refusing on a DIFFERENT list leaves the refusal reading an empty
    # trace. Each refusal's first argument must be the same NAME the nearest
    # preceding measured call handed to `leg_trace=`.
    for refusal in refusals:
        first = refusal.args[0] if refusal.args else None
        assert isinstance(first, ast.Name), (
            f"`_refuse_degraded_legs` at line {refusal.lineno} must be handed a "
            "named trace, not an expression")
        prior = [ln for ln in traces if ln < refusal.lineno]
        assert prior, (
            f"`_refuse_degraded_legs` at line {refusal.lineno} has no measured "
            "`run_fts_query` call before it")
        owner = max(prior)
        want = traces[owner]
        # ⛔ INSIDE THE SAME BLOCK, not merely after the call (#12349 review
        # cycle 7): moving the refusal out of the loop body left 299 of 300
        # measured reads unguarded and the pairing check GREEN. A refusal that
        # belongs to a measured call comes before the NEXT measured call — i.e.
        # between the call that wrote the trace and the one after it.
        nxt = [ln for ln in traces if ln > owner]
        if nxt:
            assert refusal.lineno < min(nxt), (
                f"`_refuse_degraded_legs` at line {refusal.lineno} comes AFTER "
                f"the next measured `run_fts_query` call at line {min(nxt)} — it "
                "has been hoisted out of the loop body, so the reads between "
                "them are unguarded")
        assert first.id == want, (
            f"`_refuse_degraded_legs` at line {refusal.lineno} refuses on "
            f"{first.id!r} but the leg wrote its trace to {want!r} — the refusal "
            "would read an empty list and pass every degraded read")
    # The fixture loop is the second published verdict that rests on this leg.
    assert len(refusals) >= len(legs), (
        "both the recall sweep AND the #8933 fixture loop must guard the shipped "
        "leg: an unguarded fixture read publishes a degraded read as "
        "`rank=None`, i.e. a capability FAIL")


def test_a_degraded_shipped_leg_is_a_REFUSAL_not_a_miss(tool_mod) -> None:
    """`run_fts_query` returns `[]` for a failed read as well as for a miss.

    ⛔ The direction that matters: a wedged FalkorDB (breaker open, index
    missing, query failed) collapsing the shipped baseline to ~0 publishes a
    spectacular PASS — the very trigger `#12298` step 3 pre-authorises. The
    sweep therefore reads the leg's own trace and refuses; a genuine empty
    result must still be accepted, or the measurement could never run.
    """
    for reason in ("breaker_open", "index_missing", "query_failed"):
        bad = [{"leg": "fts", "ran": False, "degraded": True,
                "reason": reason, "count": 0}]
        with pytest.raises(SystemExit) as err:
            tool_mod._refuse_degraded_legs(bad, "web_search")
        assert "REFUSED" in str(err.value) and reason in str(err.value)

    # ran, degraded=False, count 0 — the shipped leg RAN and found nothing.
    tool_mod._refuse_degraded_legs(
        [{"leg": "fts", "ran": True, "degraded": False,
          "reason": "empty_results", "count": 0}], "web_search")
    # ⛔ An ABSENT fts entry is a refusal too (`leg_trace_unavailable` is the
    # repo's own fail-closed reading of an empty trace): the guard must not
    # accept "no account" as "a clean read". A vector-only trace is that case.
    with pytest.raises(SystemExit) as err:
        tool_mod._refuse_degraded_legs([], "web_search")
    assert "NO trace entry" in str(err.value)
    with pytest.raises(SystemExit):
        tool_mod._refuse_degraded_legs(
            [{"leg": "vector", "ran": True, "degraded": True,
              "reason": "no_query_vector", "count": 0}], "web_search")


def test_brief_production_estimate_rederives_from_the_per_class_cells(
        artifact, population, brief_text) -> None:
    """§4.3's derived figures are the only published numbers §3.3 does not pin.

    The class cells they come from ARE pinned, so a re-run reds §3.3 — but §4.3
    could still go stale on its own, which is the rot class the pins exist to
    close. `+26.7` is the path class's delta; `+91.8` is the `snake_case`
    SUB-class's (1 of 73 → 68 of 73), not the whole identifier class's 74.4.
    """
    pc = {a: artifact["arms"][a]["per_class"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed")}
    path_delta = (pc["falkordb_tfidf__tokens_fixed"]["path"]["3"]
                  - pc["falkordb_tfidf"]["path"]["3"]) * 100
    assert round(path_delta, 1) == 26.7
    assert "26.7" in brief_text

    snake = [n["id"] for n in population["names"]
             if n["cls"] == "ident" and "#" not in n["name"]]
    path_cls = [n["id"] for n in population["names"] if n["cls"] == "path"]
    shipped, fixed = (artifact["arms"][a]["ranks"] for a in (
        "falkordb_tfidf", "falkordb_tfidf__tokens_fixed"))

    def hit(ranks: dict, ids: list[str]) -> int:
        return sum(1 for i in ids if ranks.get(i) is not None and ranks[i] <= 3)

    snake_delta = (hit(fixed, snake) - hit(shipped, snake)) / len(snake) * 100
    assert len(snake) == 73
    assert round(snake_delta, 1) == 91.8, snake_delta
    # ⛔ THE CELL, not a whole-document substring (#12349 review cycle 5,
    # finding 5). `"91.8" in brief_text` passed while the §4.3 CELL could be
    # rewritten to `191.8` — the exact hole this file's module docstring
    # condemns for §0, reproduced three hundred lines lower. The cells are read
    # from the row they belong to and compared to the recomputed value.
    path_delta = (hit(fixed, path_cls) - hit(shipped, path_cls)) / len(path_cls) * 100
    table_rows = _table(brief_text, "class / sub-class", "weighted")
    assert len(table_rows) == 5, [r[0] for r in table_rows]
    assert round(_num(table_rows[2][2]), 1) == 91.8, (
        f"§4.3's `snake_case` Δ cell says {table_rows[2][2]!r}; the artifact "
        f"recomputes {round(snake_delta, 1)}")
    assert round(_num(table_rows[3][2]), 1) == round(path_delta, 1), (
        f"§4.3's `path` Δ cell says {table_rows[3][2]!r}; the artifact "
        f"recomputes {round(path_delta, 1)}")

    # The sentence the table's remainder rests on, arithmetic included. The
    # shares are read from the rows the brief prints them in: the definite cell,
    # the `#n` cell, and the code-token share the `snake_case` row NAMES (that
    # cell says NOT RECORDED, which is the point of the row).
    table = table_rows
    definite = _num(table[0][1])
    hashed = _num(table[1][1])
    assert (round(definite, 1), round(hashed, 1)) == (62.3, 2.2)
    token_share = 9.0
    assert f"{token_share:.1f} %" in table[2][1], table[2][1]
    assert "NOT RECORDED" in table[2][1]
    total = definite + hashed + token_share
    assert round(total, 1) == 73.5
    assert round(100 - total, 1) == 26.5
    assert "73.5 %" in brief_text and "26.5 %" in brief_text

    # ⛔ The Δ COLUMN, in its own cells — a whole-document `"91.8" in brief` is a
    # substring that §3.4's sentence and `share × 91.8` both satisfy, so the two
    # figures §4.3 is actually quoted from could be falsified with the pin green
    # (measured by mutation on the previous cut: 26.7→26.6 and 91.8→81.8 both
    # passed).
    assert round(_num(table[2][2].split("pts")[0]), 1) == round(snake_delta, 1), (
        f"§4.3 `snake_case` Δ cell {table[2][2]!r} vs re-derived {snake_delta:.1f}")
    assert round(_num(table[3][2]), 1) == round(path_delta, 1), (
        f"§4.3 path Δ cell {table[3][2]!r} vs re-derived {path_delta:.1f}")
    assert _num(table[1][2]) == 0.0, (
        f"§4.3 `#n` row claims a non-zero effect: {table[1][2]!r} — the "
        "measured effect on that sub-class is zero")


def test_brief_reading_two_bullets_are_the_artifact(artifact, brief: str) -> None:
    """§0's Reading-2 bullets — the summary §0 tells a reader to quote from.

    ⛔ §0 is the section the PR body instructs a reader to consult before quoting
    any number, and its bullets DUPLICATE figures that §3.1–§3.3 publish. With
    only the tables pinned, a re-run that legitimately moved a §3.2 cell could
    leave these bullets quoting the superseded figures with every gate green —
    the condition the file claims is impossible (review cycle 5, finding 2).
    """
    plain = " ".join(brief.replace("**", "").split())
    cmp = artifact["comparisons"]
    k3 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    k15 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"]
    subset = artifact["verdict"]["primary_arm_tokens_controlled_subset"]
    dep = artifact["verdict"]["deployable_arm"]
    recovery = artifact["verdict"]["recovery_arm_no_index_move"]
    inplace = cmp["falkordb_tfidf__vs__falkordb_tfidf__tokens_fixed"]["k3"]

    assert (
        "with the query terms held constant, BM25 buys "
        f"+{k3['delta_pts']:.2f} pts (p = {k3['mcnemar_p_two_sided']:.2f}) overall, "
        f"+{subset['delta_pts']:.2f} pts (p = {subset['mcnemar_p_two_sided']:.2f}) on "
        "the subset where the control holds, and "
        f"+{k15['delta_pts']:.2f} pts at k=15 with "
        f"{'ZERO' if k15['discordant'] == 0 else k15['discordant']} discordant pairs"
    ) in plain, "§0's scorer bullet no longer matches the artifact's paired tests"

    shipped = artifact["arms"]["falkordb_tfidf"]["ranks"]
    tokens = artifact["arms"]["postgres_bm25/tokens"]["ranks"]
    both = [q for q in shipped if shipped[q] is not None and tokens[q] is not None]
    moved = {q: abs(shipped[q] - tokens[q]) for q in both if shipped[q] != tokens[q]}
    mean_sh = artifact["arms"]["falkordb_tfidf"]["recall_by_k"]["15"]["mean_rank_when_found"]
    mean_tk = artifact["arms"]["postgres_bm25/tokens"]["recall_by_k"]["15"]["mean_rank_when_found"]
    assert mean_sh > mean_tk, (
        "the shipped leg no longer ranks worse on mean rank — §0's 'rank differently' "
        "warning has lost its evidence")
    assert (
        f"of the {len(both)} names both arms find inside k=15, {len(moved)} rank "
        f"differently (mean rank when found {mean_sh:.3f} vs {mean_tk:.3f}, "
        f"largest move {max(moved.values())} places"
    ) in plain, "§0's window-vs-order bullet no longer matches the artifact's ranks"

    assert (
        f"the same +{recovery['delta_pts']:.2f} pts is recovered inside the current "
        f"index ({dep['recall_at_3']:.4f}, "
        f"{'zero' if inplace['only_a'] == 0 else inplace['only_a']} regressions)"
    ) in plain, "§0's recovery bullet no longer matches the artifact"

    # §4's verdict line restates the same two deltas; it is the sentence a reader
    # quotes as the RESULT, so it is pinned with them rather than left as prose.
    assert (
        "Result: FAIL — on the full arm "
        f"(+{k3['delta_pts']:.2f} pts, p = {k3['mcnemar_p_two_sided']:.3f}) and on "
        f"the tokens-controlled subset (+{subset['delta_pts']:.2f} pts, "
        f"p = {subset['mcnemar_p_two_sided']:.3f})"
    ) in plain, "§4's Result line no longer matches the artifact"


def test_the_decision_documents_status_note_is_the_artifact(artifact, search_arch: str) -> None:
    """`SEARCH-ARCHITECTURE.md` §6.2's STATUS note restates three figures.

    It is the DECISION document — the record a lane reads to learn why the text
    index did not move — and nothing opened it before this test, so a re-run could
    move every pinned figure in the brief and the changelog and leave the note
    asserting the superseded swing (review cycle 5, finding 3).
    """
    # A blockquote in the decision doc puts a `>` between the words a sentence
    # spans on two lines; drop the marker before comparing prose.
    text = " ".join(re.sub(r"(?m)^\s*>\s?", "", search_arch).split())
    cmp = artifact["comparisons"]
    k3 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    k15 = cmp["falkordb_tfidf__vs__postgres_bm25/tokens"]["k15"]
    dep = artifact["verdict"]["deployable_arm"]
    recovery = artifact["verdict"]["recovery_arm_no_index_move"]

    assert k3["mcnemar_p_two_sided"] > 0.05, (
        "the primary comparison is now significant — the status note's "
        "'not significant' is wrong; re-measure and re-decide")
    assert f"by ~{round(k3['delta_pts'])} point (not significant)" in text, (
        "the status note's '~N point (not significant)' must be the primary "
        f"comparison's delta {k3['delta_pts']} and p {k3['mcnemar_p_two_sided']}")
    assert f"by **zero** at k={15}" in text, (
        "the status note's 'zero at k=15' must be the k=15 delta "
        f"{k15['delta_pts']}")
    assert k15["delta_pts"] == 0.0
    assert f"the whole {dep['delta_pts']:g}-point swing" in text, (
        f"the status note's swing must be the deployable arm's {dep['delta_pts']}")
    assert recovery["delta_pts"] == dep["delta_pts"], (
        "the recovery arm no longer reproduces the deployable arm's swing, so the "
        "status note's 'reproduces inside the current index' is now false")


def test_the_index_wait_accepts_at_least_and_not_exactly(tool_mod) -> None:
    """The inequality is a DECISION, so it is pinned where it is written.

    ⛔ `>=` is why the 305-document index in §3.5 was accepted without a word:
    `305 >= 300` returns immediately, where `==` would have timed out and
    refused loudly. Reversing it (`<=`) would return `-1` at once and reopen the
    partially-built-index artefact the wait exists to close — with every other
    test in this file green, which is why the polarity is asserted here.
    """
    src = inspect.getsource(tool_mod._wait_indexed)
    assert "last >= expected" in src, (
        "the index wait no longer accepts an at-least-full index — re-read §3.5 "
        "and the artifacts' `measurement_notes` before changing this")
    assert "last <= expected" not in src and "last == expected" not in src, (
        "the wait became an exact match or an upper bound; a partially built "
        "index would then be measured as misses")


def test_brief_states_what_the_reuse_guards_actually_do(brief_text) -> None:
    """§2 must not over-claim the reuse guard — it did, and a merge lost the fix.

    ⛔ The code refuses a reused graph only when a STORED NAME differs, and the
    index wait accepts `numDocuments >= n`, so `--graph <a previous run's graph>`
    with the SAME population is accepted and the sweep then runs over that run's
    leftover fixture rows — the confound §3.5 and `measurement_notes` disclose.
    A brief that says "refuses a reused graph outright" tells a reproducer the
    opposite, and the claim was already lost once in a conflict resolution.
    """
    flat = " ".join(brief_text.replace("**", "").split())
    assert "refuses a reused graph outright" not in flat, (
        "§2 claims an outright reuse refusal; the harness refuses only a "
        "stale-NAME mismatch")
    assert "with the SAME population is ACCEPTED" in flat, (
        "§2 must state the case the guards do NOT cover")
    assert "refuses a graph whose stored names do not match" in flat
    assert "numDocuments >= n" in flat, (
        "§2 must name the inequality, which is why an over-full index passes")


def test_the_falsifier_predicate_is_stated_the_same_way_in_every_document(
        artifact, brief: str, changelog: str, search_arch: str) -> None:
    """One predicate, four documents — and the artifact is what decides it.

    ⛔ This is not a style rule. `#12298`'s falsifier sentence states a CLOSURE
    condition ("BM25 does **not** beat the current scorer ⇒ the leg is closed and
    no SPLADE work happens"). A deployable arm that BEATS the scorer leaves that
    condition **unmet** — while the *pre-authorisation's* trigger ("if M3 passes,
    proceed into SPLADE") IS satisfied. Writing the two as one word ("the
    falsifier is satisfied by the deployable arm") inverts the premise the whole
    deferral rests on: it reads as "the closure was right, C3 stays closed", the
    opposite of what the measurement says. It appeared in five places at once and
    was repaired in stages; nothing pinned the predicate, so it could flip back
    silently (review cycle 6).
    """
    dep = artifact["verdict"]["deployable_arm"]
    # The FACT the wording must follow: the deployable arm beats the shipped leg,
    # so the closure condition is not triggered and the trigger is.
    assert dep["recall_at_3"] > dep["recall_at_3_base"], (
        "the deployable arm no longer beats the shipped scorer — the whole "
        "'closure not triggered' reading reverses; re-measure and re-decide")

    inverted = re.compile(r"falsifier[^.]*?is\s+satisfied\s+by", re.I)
    for name, doc in (("research-brief.md", brief),
                      ("SEARCH-ARCHITECTURE-CHANGELOG.md", changelog),
                      ("SEARCH-ARCHITECTURE.md", search_arch)):
        plain = " ".join(doc.replace("**", "").replace("*", "").split())
        assert re.search(r"not\s+met|not\s+triggered", plain, re.I), (
            f"{name} no longer states that the closure condition is not met/"
            f"triggered — the predicate must be spelled out where a reader takes "
            f"the C3 ruling from")
        assert not inverted.search(plain), (
            f"{name} calls the falsifier 'satisfied by' the arm — that is the "
            f"closure condition, which the deployable arm leaves UNSATISFIED; only "
            f"the pre-authorisation's trigger is satisfied")
