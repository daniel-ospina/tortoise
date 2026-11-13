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
   decomposition — and the same numbers repeated in
   `docs/architecture/SEARCH-ARCHITECTURE-CHANGELOG.md` are compared too. A
   re-run that moves a number therefore reds here instead of leaving prose that
   the committed artifact contradicts (#12298 review cycle 2, findings 1–3: an
   earlier revision compared FOUR values by whole-document substring and claimed
   in three places that every headline number was covered — a coverage claim the
   pin did not enforce is the silent-rot class this file exists to close).

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
    passing: the primary arm is the scorer-controlled one, and the rule was
    fixed before the numbers existed.
    """
    primary = artifact["comparisons"][
        "falkordb_tfidf__vs__postgres_bm25/tokens"]["k3"]
    assert primary["mcnemar_p_two_sided"] > 0.05, (
        "the scorer control is now significant — re-run and re-decide")
    assert artifact["verdict"]["M3"] == "FAIL"
    # ⛔ The issue's OWN falsifier sentence is satisfied by the deployable arm.
    # The brief must present BOTH readings; this pin makes the second one
    # impossible to drop when the first is quoted.
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


def test_the_tie_rule_is_matched(artifact) -> None:
    """Requirement 6: both stores break score ties the same way (id DESC)."""
    for arm in ("postgres_bm25/tokens", "postgres_bm25/native"):
        assert "id DESC" in artifact["arms"][arm]["engine"], (
            f"{arm} does not state the tie key it ordered by")
    assert "DESC on every label" in artifact["verdict"]["requirements"]["6_tie_rule"]


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
        "abc_def returning zero rows for its own name is the crispest case in "
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
        return sum(1 for i in ids if ranks.get(i) is not None and ranks[i] < 3)

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
