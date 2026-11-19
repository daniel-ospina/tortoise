"""Pins the structural facts the #12186 capstone rests on, and its artifact.

The capstone's claim is *"the merged levers are visible to a user"*, and it is
made twice: once in prose (`docs/evidence/12186-capstone/README.md`) and once in
the committed `measurement.json`. Two ways that record rots, and this module
closes both:

1. **The definitions drift.** A duplicate rate is meaningless without its rule,
   and the near-duplicate rule is a *choice* (`neardup-v1`) that a later
   "simplification" could change while every published number stayed put. The
   rule, the window arithmetic and the pin's refusals are asserted here.
2. **The README drifts away from the artifact.** Every figure in the README's
   plain-language summary and in its §1 provenance, §2 recall / per-class /
   paired / leg-degradation, §3a duplicate-rate, §3b mint and §4 lever tables is
   recomputed from the committed artifact and compared — CELL-scoped for the
   tabular ones, ROW-scoped and digit-bounded for the prose ones — so no figure in
   those places can disagree with the evidence.
   ⛔ **This is the boundary of the claim, and it is deliberately narrower than
   "the whole document".** Figures in §5's declared limits and §6's run metadata
   are NOT pinned. Every earlier cut of this paragraph claimed more than it
   enforced, and each gap was found by mutating the document: the first said
   "every figure the README's tables state" while §3a's count columns had no
   assertion at all; the second left §1 and §4 unpinned while calling them "not
   re-derivable" (they are, and §4 holds the SECOND quotation of the mint rate);
   the third pinned only §4's figures and left its `shipped state` and verdict
   WORD unpinned, so `OFF by default` could become `ON by default` with every pin
   green. **A claim wider than its measurement is the defect this file exists to
   avoid**, so when adding a row here, mutate the cell first.

⛔ Nothing here runs the measurement — it needs a live FalkorDB and ~10 minutes of
queries. That is deliberate (the same reason
`tests/test_candidate_recall_11901.py` gives): a test that needed a graph would be
skipped on CI and would guard nothing. What is pinned is the reasoning surface.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
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
    # ⛔ EQUALITY, not presence. The README's headline is `sha256 2486a359…,
    # pinned in the artifact`, and "pinned" has to mean THIS file: a presence
    # check passes any digest at all (a foreign sha256 stayed green before this
    # line), which is the difference between an identity and a field being set.
    assert pin["sha256"] == hashlib.sha256(PIN.read_bytes()).hexdigest()
    assert pin["queries"] == len(json.loads(PIN.read_text())["query_ids"])


def test_the_artifact_declares_the_door_it_measured(artifact):
    """The sentence and the binding it claims must be the same fact.

    `user_door_measured` is DERIVED from `user_door` (`_user_door_measured`), so
    an artifact cannot assert it measured the user's door while its own binding
    says the registry moved. Pinning the committed record here closes the gap the
    live-registry test leaves: that one checks TODAY's registry, this one checks
    what the committed run claims.
    """
    door = artifact["user_door"]
    assert door["present"] is True
    assert door["matches_measured_call"] is True
    assert artifact["merged_lever_visibility"]["user_door_measured"] == \
        cap._user_door_measured(door)


def test_a_binding_that_is_not_the_measured_call_never_renders_as_measured():
    # The pre-fix shape was a literal string, so a binding of `present: False`
    # still produced "we measured the user's door".
    assert cap._user_door_measured({"present": False}).startswith("NOT-MEASURED")
    assert cap._user_door_measured({"present": True,
                                    "matches_measured_call": False,
                                    "sdk_method": "other",
                                    "tool": "tortoise_search",
                                    "rest": "GET /other"}).startswith("NOT-MEASURED")
    assert cap._user_door_measured({"present": True,
                                    "matches_measured_call": True,
                                    "sdk_method": "tortoise_fts_query",
                                    "tool": "tortoise_search",
                                    "rest": "GET /v1/search"}) == (
        "sdk.tortoise_fts_query = MCP tortoise_search / REST GET /v1/search")


def test_run_refuses_a_binding_that_is_not_the_users_door(monkeypatch):
    """`run()` must not write an artifact it cannot back with the binding."""
    import argparse
    monkeypatch.setattr(cap, "_user_door_binding",
                        lambda: {"tool": "tortoise_search", "present": False})
    args = argparse.Namespace(pin=str(PIN), graph="cap-test", namespace=None,
                              out=None)
    assert cap.run(args) == 3


def test_the_artifact_declares_the_graph_residue(artifact):
    """`sdk.close()` is the only teardown, so the residue is DECLARED.

    A run leaves one POPULATED graph on whatever FalkorDB `TORTOISE_DB_URI`
    names; a shared server accumulates one full set per run (#5084). The
    sibling instrument (`tools/window_quality_m2.py`) records the same block. A
    silent run would leave a reader sizing the lane's footprint with no number.
    """
    cleanup = artifact["cleanup"]
    assert cleanup["graphs_created"] == 1
    assert cleanup["graphs_dropped"] == 0
    assert cleanup["left_in_place"] == [artifact["graph"]["name"]]
    assert cleanup["note"].strip()
    # The tool's own builder is what the next run emits — pin the shape, not just
    # the committed instance of it.
    built = cap._cleanup("g1")
    assert built["graphs_created"] == built["graphs_dropped"] + len(
        built["left_in_place"])


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
#
# ⛔ CELL-SCOPED, not substring-scoped. A bare `f"{value}" in text` reads the whole
# 226-line document, so a misquoted table CELL stays green whenever the same
# digits survive in prose or in another row (observed: the rerank row's
# `0.9733` → `0.9533` passed because a sentence quotes `0.9733`; deleting a whole
# arm row passed because its numbers appeared in the sentence too). Every pin
# below parses the table it is about, maps each DATA ROW to ONE arm, and compares
# the cell — so a dropped row and a shifted decimal both red.

#: How a table row is recognised as belonging to one arm. Two markers, both
#: matched CASE-SENSITIVELY against the row's label cell — `SHIPPED` is the shipped
#: user-door row and `shipped` (lower case) is the capture prior's, which keeps the
#: rerank row's "NOT the shipped default" from matching either.
ARM_ROW_MARKERS: dict[str, tuple[str, ...]] = {
    "capture_prior_pre_lever": ("capture prior", "TORTOISE_UNDERSCORE_TERMS=0"),
    "capture_prior": ("capture prior", "shipped"),
    "user_search_pre_lever": ("user door", "TORTOISE_UNDERSCORE_TERMS=0"),
    "user_search": ("user door", "SHIPPED"),
    "user_search_rerank": ("user door", "fused rerank"),
}

#: The paired table's rows, by the artifact key they carry.
PAIR_ROW_MARKERS: dict[str, tuple[str, ...]] = {
    "capture_prior_pre_lever -> capture_prior": ("#12368` on the **capture prior**",),
    "user_search_pre_lever -> user_search": ("#12368` on the **user door**",),
    "user_search -> user_search_rerank": ("#12297` rerank on the user door",),
    "capture_prior -> user_search": ("**the door itself**",),
}


def _tables(text: str, header_contains: str) -> tuple[list[str], list[list[str]]]:
    """The (header, data rows) of the markdown table whose header matches."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not line.lstrip().startswith("|") or header_contains not in line:
            continue
        header = [c.strip() for c in line.strip().strip("|").split("|")]
        rows: list[list[str]] = []
        j = i + 2  # skip the `|---|` separator
        while j < len(lines) and lines[j].lstrip().startswith("|"):
            rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
            j += 1
        return header, rows
    raise AssertionError(f"no markdown table with a header containing {header_contains!r}")


def _number(cell: str) -> float:
    """A table cell as a number: strip emphasis/markup, keep the SIGN."""
    clean = (cell.replace("*", "").replace("`", "").replace(",", "")
             .replace("\u2212", "-").replace("\u00a0", " ").strip())
    return float(clean)


def _one_row(rows: list[list[str]], markers: tuple[str, ...], what: str) -> list[str]:
    matched = [r for r in rows if all(m in r[0] for m in markers)]
    assert len(matched) == 1, (
        f"expected exactly one row for {what} (markers {markers}), got "
        f"{len(matched)} of {len(rows)}: {[r[0] for r in matched]}")
    return matched[0]


def _assert_rows_cover(rows: list[list[str]], markers: dict[str, tuple[str, ...]]) -> None:
    """Every data row is claimed by an arm, and every arm claims one row.

    This is what makes a DELETED or ADDED row red even if the numbers it carried
    survive in the prose: the row set itself is pinned.
    """
    claimed = set()
    for arm, marks in markers.items():
        row = _one_row(rows, marks, arm)
        claimed.add(id(row))
    assert len(claimed) == len(rows), (
        f"{len(rows) - len(claimed)} data row(s) belong to no declared arm: "
        f"{[r[0] for r in rows if id(r) not in claimed]}")


def test_the_readme_quotes_the_recall_the_artifact_holds(artifact):
    """Every arm's row, cell by cell — the epic's headline table."""
    header, rows = _tables(README.read_text(), "recall@3")
    assert [h for h in header[1:]] == [f"recall@{k}" for k in cap.KS], header
    _assert_rows_cover(rows, ARM_ROW_MARKERS)
    for arm, marks in ARM_ROW_MARKERS.items():
        row = _one_row(rows, marks, arm)
        assert len(row) == 1 + len(cap.KS), (arm, row)
        for col, k in enumerate(cap.KS, start=1):
            expected = artifact["arms"][arm]["recall"][str(k)]
            assert _number(row[col]) == pytest.approx(expected), (
                f"README row {arm!r} quotes recall@{k}={row[col]}, artifact "
                f"holds {expected}")


def test_the_readme_quotes_the_per_class_recall_the_artifact_holds(artifact):
    """§2's per-class table is the epic's MECHANISM claim — `ident` +53.3 pts.

    Nothing read `arms[*]["by_class"]` before this, so `0.4111` → `0.5111` on the
    headline mechanism cell reddened nothing (observed).
    """
    header, rows = _tables(README.read_text(), "pre-`#12368` @3")
    assert header[1:] == ["pre-`#12368` @3", "shipped @3",
                          "pre-`#12368` @15", "shipped @15"], header
    #: column → (arm, k), in the table's own order.
    columns = ((1, "user_search_pre_lever", 3), (2, "user_search", 3),
               (3, "user_search_pre_lever", 15), (4, "user_search", 15))
    classes = list(cap_class_names(artifact))
    assert len(rows) == len(classes), (len(rows), classes)
    for row in rows:
        cls = next((c for c in classes if f"`{c}`" in row[0]), None)
        assert cls is not None, f"unmapped per-class row: {row[0]!r}"
        n = artifact["arms"]["user_search"]["by_class"][cls]["n"]
        assert f"({n})" in row[0], f"{cls}: README label omits its n={n}"
        for col, arm, k in columns:
            expected = artifact["arms"][arm]["by_class"][cls][str(k)]
            assert _number(row[col]) == pytest.approx(expected), (
                f"README {cls} col {header[col]} = {row[col]}, artifact holds "
                f"{arm}@{k}={expected}")


def cap_class_names(artifact: dict) -> tuple[str, ...]:
    return tuple(artifact["arms"]["user_search"]["by_class"])


def test_the_readme_quotes_the_leg_degradation_the_artifact_holds(artifact):
    """The raw column's noise, per arm — the second most-quoted number in the doc.

    The pre-fix doc gave the RERANK arm the shipped arm's counts (18 keyword-only
    / 6 empty, copied a paragraph down from `user_search`'s own 18/6); the
    artifact holds 14/8 for the rerank. A count is exactly the kind of figure a
    paragraph silently reattributes, so it is pinned per arm here.
    """
    header, rows = _tables(README.read_text(), "keyword-only windows")
    assert header[1:] == ["keyword-only windows (of 300)",
                          "queries with no rows at all",
                          "`vector:degraded:timeout`"], header
    _assert_rows_cover(rows, ARM_ROW_MARKERS)
    for arm, marks in ARM_ROW_MARKERS.items():
        row = _one_row(rows, marks, arm)
        expected = (artifact["arms"][arm]["window_modes"]["keyword_only"],
                    artifact["arms"][arm]["rows_zero"]["3"],
                    artifact["arms"][arm]["leg_degrade_reasons"].get(
                        "vector:degraded:timeout", 0))
        assert tuple(int(_number(c)) for c in row[1:4]) == expected, (
            f"README degradation row {arm!r} = {row[1:4]}, artifact holds {expected}")


def test_the_readme_quotes_the_paired_deltas_the_artifact_holds(artifact):
    """Signed, cell by cell — a loss written as a gain must red.

    Pre-fix this was `f"{abs(delta):.2f}" in text`, so `+15.47 pts` → `-15.47 pts`
    (publishing the lever's gain as a loss) stayed green (observed). `abs()` is
    the one thing a delta's sign cannot survive.
    """
    header, rows = _tables(README.read_text(), "clean queries")
    assert header[1:4] == ["clean queries", "before → after (recall@3)", "Δ"], header
    _assert_rows_cover(rows, PAIR_ROW_MARKERS)
    for name, marks in PAIR_ROW_MARKERS.items():
        row = _one_row(rows, marks, name)
        cell = artifact["paired"][name]["paired"]

        clean = row[1].replace("*", "")
        compared, excluded = clean.split("(")
        assert int(compared.strip()) == cell["queries_compared"], (name, row[1])
        assert int(excluded.split()[0]) == cell["excluded_degraded_on_either_side"]

        before, after = row[2].replace("*", "").split("→")
        assert _number(before) == pytest.approx(cell["before_recall_clean"])
        assert _number(after) == pytest.approx(cell["after_recall_clean"])

        # ⛔ the SIGNED delta: `_number` keeps the sign, so a published loss reds
        delta = _number(row[3].replace("pts", ""))
        assert delta == pytest.approx(cell["delta_pts_clean"]), (
            f"README delta for {name} is {row[3]}, artifact holds "
            f"{cell['delta_pts_clean']:+}")

        gains, losses = row[4].replace("*", "").split("/")
        assert int(_number(gains)) == cell["gains"], (name, row[4])
        assert int(_number(losses)) == cell["losses"], (name, row[4])

        p = row[5].replace("*", "").strip()
        if p.startswith("<"):
            assert cell["p"] < float(p[1:].strip())
        else:
            assert float(p) == pytest.approx(cell["p"])


def _count(cell: str) -> int:
    """A COUNT cell: emphasis/backticks/list-spacing stripped, thousands separators gone.

    The README spells counts both ways in the same document (`875` and `1 452`),
    so a count cell cannot go through `_number`, which keeps the space and then
    refuses `1 452`. Kept separate from `_number` on purpose — widening `_number`
    would change what every FLOAT pin above accepts.

    ⛔ REFUSES a fractional spelling instead of truncating it: `int(float("192.9"))`
    is 192, which would let a cell reading `192.9` compare equal to the artifact's
    `attached: 192` and stay green (measured — that is the false pass this
    refusal exists to close). A count is an integer or the prose is wrong.
    """
    clean = (cell.replace("*", "").replace("`", "").replace(",", "")
             .replace("\u00a0", "").replace("\u202f", "").replace(" ", "").strip())
    assert re.fullmatch(r"[+-]?\d+", clean), (
        f"not an integer count cell: {cell!r} -> {clean!r}")
    return int(clean)


def _flat(text: str) -> str:
    """One table ROW as a comparison surface: markup, spacing and separators gone.

    For the rows whose value is a SENTENCE rather than a column (`§1`'s corpus line,
    `§4`'s lever verdicts) a cell index would pin the prose's layout. Flattening the
    row and requiring the artifact-derived token INSIDE THAT ROW keeps the pin
    row-scoped — which is what makes a dropped row, or a figure moved to another
    lever, red — without pinning the sentence's shape.

    ⛔ A flattened row CONCATENATES its neighbours, so a plain `token in row` is an
    unbounded substring: `41 gains` -> `141 gains`, `104 files` -> `1 104 files` and
    `0.5328` -> `0.53281` all stayed green through one (measured). Use
    `_mentions`, which refuses a match whose own leading or trailing digit is
    glued to another digit.
    """
    return (text.replace("*", "").replace("`", "").replace(",", "")
            .replace("\u00a0", "").replace("\u202f", "").replace(" ", ""))


def _mentions(text: str, token: str) -> bool:
    """`token` occurs in `text` with no extra digit glued to a number in it.

    The boundary is on the TOKEN's own edges, which is what makes an inflated
    figure red while leaving the digit-that-belongs-to-the-next-token alone:
    `"122912records0parseerrors"` still mentions `"122912records"` (its last char
    is a letter), while `"1122912records"` does NOT (its first char is a digit and
    the match is preceded by one).
    """
    for m in re.finditer(re.escape(token), text):
        if token[0].isdigit() and m.start() and text[m.start() - 1].isdigit():
            continue
        if token[-1].isdigit() and m.end() < len(text) and text[m.end()].isdigit():
            continue
        return True
    return False


def _assert_mentions(text: str, tokens, where: str) -> None:
    for token in tokens:
        assert _mentions(text, token), f"{where} omits {token!r}: {text!r}"


def test_the_readme_quotes_the_duplicate_rates_the_artifact_holds(artifact):
    """§3a, CELL BY CELL — the rate, and the three counts it is computed from.

    This used to be `f"{rate:.4f}" in text`, which is the exact shape the block
    above forbids. Measured at this head: `875` → `999` on the k=3 row's `rows
    handed out` and `11` → `77` on its pair count both stayed GREEN, because the
    pin compared a formatted float against the whole 226-line document and never
    read the counts. A duplicate rate whose denominator can be misquoted is a
    number nobody can re-derive, so the counts are pinned with it.
    """
    header, rows = _tables(README.read_text(), "rows handed out")
    assert header[1:3] == ["rows handed out", "near-duplicate rows"], header
    assert header[4:] == ["duplicate pairs", "queries affected"], header
    assert len(rows) == len(cap.KS), ([r[0] for r in rows], list(cap.KS))
    by_k = {r[0].replace("*", "").strip(): r for r in rows}
    assert set(by_k) == {str(k) for k in cap.KS}, sorted(by_k)
    for k in cap.KS:
        row = by_k[str(k)]
        held = artifact["arms"]["user_search"]["duplicate_rate_in_window"][str(k)]
        assert (_count(row[1]), _count(row[2])) == (
            held["rows"], held["duplicate_rows"]), (
            f"README dup row k={k} counts ({row[1]}, {row[2]}), artifact holds "
            f"rows={held['rows']} duplicate_rows={held['duplicate_rows']}")
        assert _number(row[3]) == pytest.approx(held["rate"]), (
            f"README dup row k={k} rate {row[3]}, artifact holds {held['rate']}")
        assert _count(row[4]) == held["pairs"], (
            f"README dup row k={k} pairs {row[4]}, artifact holds {held['pairs']}")
        affected = row[5].split("/")
        assert len(affected) == 2, (k, row[5])
        assert (_count(affected[0]), _count(affected[1])) == (
            held["queries_with_any"], held["queries"]), (
            f"README dup row k={k} affected {row[5]!r}, artifact holds "
            f"{held['queries_with_any']}/{held['queries']}")


#: The §3b mint table's two configuration rows, by the artifact key they carry.
MINT_ROW_MARKERS: dict[str, tuple[str, ...]] = {
    "norm_only": ("frozen pre-",),
    "shipped": ("shipped matcher",),
}


def test_the_readme_quotes_the_mint_rates_the_artifact_holds(artifact):
    """§3b's configuration table, CELL BY CELL — `192 of 411` is the whole finding.

    Cell-scoping matters for the rate here too, and was MEASURED missing: `0.5328`
    is quoted a second time in §4's lever table, so mutating THIS table's cell to
    `0.7777` stayed GREEN under the old whole-document substring check while the
    document published two different mint rates for the shipped matcher.
    """
    mint = artifact["arms"]["mint_rate"]
    header, rows = _tables(README.read_text(), "canonical in window")
    assert [h.replace("*", "").strip() for h in header[1:]] == [
        "pairs", "canonical in window", "attached", "mint rate"], header
    assert len(rows) == len(MINT_ROW_MARKERS), [r[0] for r in rows]
    _assert_rows_cover(rows, MINT_ROW_MARKERS)
    for key, marks in MINT_ROW_MARKERS.items():
        row = _one_row(rows, marks, key)
        assert _count(row[1]) == mint["pairs"], (key, row[1], mint["pairs"])
        assert _count(row[2]) == mint["retrieval_in_window"], (
            key, row[2], mint["retrieval_in_window"])
        assert _count(row[3]) == mint[key]["attached"], (
            f"README mint row {key!r} attaches {row[3]}, artifact holds "
            f"{mint[key]['attached']}")
        assert _number(row[4]) == pytest.approx(mint[key]["rate"]), (
            f"README mint row {key!r} rate {row[4]}, artifact holds "
            f"{mint[key]['rate']}")


def test_the_readme_quotes_the_mint_rates_by_variant_class(artifact):
    """§3b's per-variant-class table, cell by cell — where the remaining gap is NAMED.

    `space-to-hyphen`'s 1.0000 is a recorded decision's boundary (`#12047` r4 f5),
    so a reader has to be able to trust the row's own counts: `180` pairs, `175`
    in window, `0` attached. Under the old whole-document check every one of those
    counts could be wrong while the document still quoted the right rate somewhere.
    """
    by_class = artifact["arms"]["mint_rate"]["by_class"]
    header, rows = _tables(README.read_text(), "variant class")
    assert header[1:] == ["pairs", "in window", "shipped attaches",
                          "mint rate"], header
    assert len(rows) == len(by_class), ([r[0] for r in rows], sorted(by_class))
    for cls, held in by_class.items():
        matched = [r for r in rows if f"`{cls}`" in r[0]]
        assert len(matched) == 1, (
            f"expected exactly one README row for variant class {cls!r}, got "
            f"{len(matched)}: {[r[0] for r in rows]}")
        row = matched[0]
        assert _count(row[1]) == held["pairs"], (
            f"README {cls} pairs {row[1]}, artifact holds {held['pairs']}")
        assert _count(row[2]) == held["retrieval_in_window"], (
            f"README {cls} in-window {row[2]}, artifact holds "
            f"{held['retrieval_in_window']}")
        assert _count(row[3]) == held["shipped_attached"], (
            f"README {cls} attaches {row[3]}, artifact holds "
            f"{held['shipped_attached']}")
        assert _number(row[4]) == pytest.approx(held["shipped_mint_rate"]), (
            f"README {cls} mint rate {row[4]}, artifact holds "
            f"{held['shipped_mint_rate']}")


def test_the_readme_quotes_the_provenance_the_artifact_records(artifact):
    """§1, ROW BY ROW — the corpus read, the population, the query sample, the digests.

    These are the numbers that make the artifact reproducible at all, and the cut
    before this one called them "not re-derivable values" and left them unpinned.
    They ARE in the artifact (`pin.corpus.*`, `pin.population`, `pin.shares`,
    `pin.digest`/`query_digest`/`sha256`) and nothing compared them to the prose:
    measured, `104 files` -> `999 files` and the population digest -> `deadbeef`
    both stayed green.

    Row-scoped rather than cell-indexed, because these values live inside prose
    sentences — so the pin holds the FIGURES without pinning the sentence's shape,
    while a dropped or reordered row still reds via `_assert_rows_cover`.
    """
    pin = artifact["pin"]
    corpus = pin["corpus"]
    header, rows = _tables(README.read_text(), "field")
    assert [h.replace("*", "").strip() for h in header] == ["field", "value"], header
    _assert_rows_cover(rows, PROVENANCE_ROW_MARKERS)

    read = _flat(_one_row(rows, PROVENANCE_ROW_MARKERS["corpus read"], "corpus read")[1])
    _assert_mentions(read, (f"{corpus['files']}files",
                            f"{corpus['lines']}records",
                            f"{corpus['parse_errors']}parseerrors",
                            f"{corpus['records_without_text']}recordswithouttext",
                            f"{int(corpus['elapsed_s'])}s"), "§1 corpus row")

    pop = _flat(_one_row(rows, PROVENANCE_ROW_MARKERS["population"], "population")[1])
    #: The per-class split, from the PIN FILE's own rows — not from a total, which
    #: is all this pin checked before. `600 definite` -> `999 definite` stayed green
    #: through that (measured). Deriving it here also pins that the split and the
    #: total agree, which the README states in one sentence.
    counts = Counter(r["cls"] for r in json.loads(PIN.read_text())["population_rows"])
    assert sum(counts.values()) == pin["population"], (
        f"the pin file's class counts sum to {sum(counts.values())}, artifact "
        f"records population {pin['population']}")
    _assert_mentions(pop, [f"{pin['population']}names"]
                     + [f"{counts[c]}{c}" for c in ("definite", "ident", "path")],
                     "§1 population row")

    queries = _flat(_one_row(rows, PROVENANCE_ROW_MARKERS["queries"], "queries")[1])
    shares = "/".join(str(pin["shares"][c]) for c in ("definite", "ident", "path"))
    _assert_mentions(queries, (f"{pin['queries']}-name", shares), "§1 queries row")

    digest = _flat(_one_row(rows, PROVENANCE_ROW_MARKERS["digest"], "digest")[1])
    #: `sha256<prefix>…` rather than the bare prefix: `2486a359e` is a LONGER hex
    #: prefix of the same digest, so the bare token does not red on it (measured).
    #: The label is part of the token because `sha256` itself ENDS in a digit, which
    #: would otherwise read as a glued digit run and make `_mentions` refuse a
    #: correct row (observed).
    _assert_mentions(digest, (pin["digest"], pin["query_digest"],
                              f"sha256{pin['sha256'][:8]}…"), "§1 digest row")


#: §1's provenance rows, by the `field` label each carries.
PROVENANCE_ROW_MARKERS: dict[str, tuple[str, ...]] = {
    "rules": ("rules",),
    "corpus read": ("corpus read",),
    "population": ("population",),
    "queries": ("queries",),
    "digest": ("digest",),
}

#: §4's lever rows, by the lever each names. Keyed on the ISSUE ID in the row's
#: label cell, which is what the artifact's own blocks are keyed by.
LEVER_ROW_MARKERS: dict[str, tuple[str, ...]] = {
    "underscore": ("#12368",),
    "fold": ("#12304",),
    "rerank": ("#12343",),
    "verdict": ("#12348",),
    "m3": ("#12349",),
}


#: §4's two left columns, as PREFIXES each row must carry. `#12368`'s and
#: `#12343`'s `shipped state` is DERIVED from `merged_lever_visibility` (see the
#: test); the rest are DECLARATIONS the artifact cannot carry — no boolean exists
#: for the write-path fold, and the two unmeasured levers are unmeasured by
#: construction — so they are declared here and pinned the same way, which is what
#: stops a prose edit promoting `#12349` from `n/a` to `YES`.
LEVER_COLUMN_PREFIXES: dict[str, tuple[str, str]] = {
    "underscore": ("", "YES"),
    "fold": ("ON", "YES"),
    "rerank": ("", "NO"),
    "verdict": ("ON", "not"),
    "m3": ("measured", "n/a"),
}


def test_the_readme_quotes_the_lever_verdicts_the_artifact_holds(artifact):
    """§4, ROW BY ROW — the table a reader leaves with, lever by lever.

    ⛔ This is the SECOND quotation of the mint rate (`libre` §4 `1.0000 -> 0.5328`
    against §3b's own copy), and the cut before this one explicitly named that
    second quotation as the masking mechanism while pinning only §3b — so editing
    §4 alone still published two different mint rates for the same matcher with
    every pin green (measured: `0.5328` -> `0.7777`, `+15.47` -> `+11.11`, and
    `41 gains` -> `99 gains` all SURVIVED). Every figure here is in the artifact
    (`merged_lever_visibility`, `arms.mint_rate`), so it is re-derivable and is
    pinned as such.
    """
    header, rows = _tables(README.read_text(), "shipped state")
    assert [h.replace("*", "").strip() for h in header] == [
        "lever", "shipped state", "visible to a user?", "evidence"], header
    _assert_rows_cover(rows, LEVER_ROW_MARKERS)

    visibility = artifact["merged_lever_visibility"]
    #: The artifact's shipped-state booleans, as the words the row must start with.
    #: Before this the pin read ONLY the third column's figures, so `**OFF by
    #: default**` -> `**ON by default**` and `**NO**` -> `**YES**` on the rerank row
    #: both stayed green (measured) — the capstone's own conclusion, invertible in
    #: prose with every pin passing. The `#12349` row is the third case: mutating
    #: `n/a — no code shipped` to `**YES** — no code shipped` also survived, which is
    #: exactly the "promoted to visible by a prose edit" this test said it prevented.
    derived_shipped = {
        "underscore": visibility["underscore_terms_shipped_default"],
        "rerank": visibility["fused_rerank_shipped_default"],
    }
    assert visibility["fused_rerank_ran_in_user_search"] is False, (
        "the artifact no longer records the rerank as un-run in user search, so "
        "§4's `NO` verdict has no premise")
    for key, marks in LEVER_ROW_MARKERS.items():
        row = _one_row(rows, marks, key)
        shipped = _flat(row[1])
        verdict = _flat(row[2])
        want_shipped = derived_shipped.get(key, LEVER_COLUMN_PREFIXES[key][0])
        assert shipped.startswith(want_shipped), (
            f"§4 `{key}` shipped state {row[1]!r} does not start with "
            f"{want_shipped!r}")
        want_verdict = LEVER_COLUMN_PREFIXES[key][1]
        assert verdict.startswith(want_verdict), (
            f"§4 `{key}` verdict {row[2]!r} does not start with {want_verdict!r} "
            "— a lever cannot be promoted to visible by a prose edit")

    underscore = visibility["underscore_terms_visible_on_user_door"]
    row = _flat(_one_row(rows, LEVER_ROW_MARKERS["underscore"], "#12368")[2])
    _assert_mentions(row, (f"+{underscore['delta_pts_clean']:.2f}pts",
                           f"{underscore['gains']}gains",
                           f"{underscore['losses']}losses",
                           f"p{underscore['p_display']}"), "§4 `#12368` row")

    mint = artifact["arms"]["mint_rate"]
    row = _flat(_one_row(rows, LEVER_ROW_MARKERS["fold"], "#12304")[2])
    _assert_mentions(row, (f"{mint['norm_only']['rate']:.4f}",
                           f"{mint['shipped']['rate']:.4f}"),
                     "§4 `#12304` row")

    rerank = visibility["fused_rerank_effect_on_user_door_if_armed"]
    row = _flat(_one_row(rows, LEVER_ROW_MARKERS["rerank"], "#12343")[2])
    _assert_mentions(row, (f"+{rerank['delta_pts_clean']:.2f}pts",
                           f"p={rerank['p_display']}"), "§4 `#12343` row")


def test_the_plain_language_summary_states_the_figures_the_artifact_holds(artifact):
    """The document's ABSTRACT — the paragraph nine readers in ten stop at.

    It is not a table, so nothing read it, and it restates the capstone's three
    verdicts in prose: `83.4 % -> 98.9 %`, `+15.5 points`, `41 queries gained`,
    `+1.13 points`, `100 % -> 53.3 %`. Measured surviving without this pin:
    `83.4 %` -> `63.4 %` and `100 % -> 53.3 %` -> `100 % -> 93.3 %`.

    Extracted by its own leading marker, then pinned digit-boundedly, so the
    wording can be rewritten freely but no figure in it can drift from the
    artifact.
    """
    lines = README.read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("**Plain language:**"))
    paragraph = []
    for ln in lines[start:]:
        if not ln.strip():
            break
        paragraph.append(ln)
    text = _flat(" ".join(paragraph))
    assert text, "the plain-language summary is empty"

    visibility = artifact["merged_lever_visibility"]
    underscore = visibility["underscore_terms_visible_on_user_door"]
    rerank = visibility["fused_rerank_effect_on_user_door_if_armed"]
    mint = artifact["arms"]["mint_rate"]
    _assert_mentions(
        text,
        (f"{underscore['before_recall_clean'] * 100:.1f}%",
         f"{underscore['after_recall_clean'] * 100:.1f}%",
         f"+{underscore['delta_pts_clean']:.1f}points",
         f"{underscore['gains']}queriesgained",
         f"{underscore['losses']}lost",
         f"+{rerank['delta_pts_clean']:.2f}points",
         f"{rerank['gains']}gained",
         f"p={rerank['p_display']}",
         f"{mint['norm_only']['rate'] * 100:.0f}%",
         f"{mint['shipped']['rate'] * 100:.1f}%"),
        "the plain-language summary")


def test_the_readme_refuses_the_cross_corpus_comparison_too(artifact):
    """The artifact refuses it; the prose must not invite it either."""
    text = README.read_text()
    assert "0.7133" in text
    assert "refuses that comparison" in text or "not a comparison to the 0.7133" in text
