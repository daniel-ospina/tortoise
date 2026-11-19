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
   tabular ones, CLAUSE-scoped for the abstract's three sentences, and
   ROW-scoped with digit-bounded tokens for the prose rows — so no figure in those
   places can disagree with the evidence.
   ⛔ **This is the boundary of the claim, and it is not "the whole document".**

   PINNED: every figure in the tables named above; the plain-language summary's three
   lever clauses and its three-lever counts; and three §2/§3 NARRATIVE restatements that
   repeat a tabulated figure (`+53.3 points`, the `0.8` Jaccard, `192 of 411`).

   NOT PINNED, and the list is deliberate: narrative prose elsewhere in §1-§4 — the
   per-arm degradation counts quoted in §2a's commentary, `~2.3 %`, `+1.7 and +6.7
   points`, `0 gains, 0 losses across 272`, `282/300`, `34 of 300` — plus §5's declared
   limits and §6's run metadata. A reader who needs a figure guarded must check it is in
   a pinned table; do NOT read this module as "the document is enforced".

   Every earlier cut of this paragraph claimed more than it enforced, and each gap was
   found by mutating the document: the first said "every figure the README's tables
   state" while §3a's count columns had no assertion at all; the second left §1 and §4
   unpinned while calling them "not re-derivable" (they are, and §4 holds the SECOND
   quotation of the mint rate); the third pinned only §4's figures and left its `shipped
   state` and verdict WORD unpinned; the fourth pinned the word but not the phrase; the
   fifth pinned the abstract's figures but not its CAPABILITY statement (`not visible to
   a user, because it is off by default` was invertible) and declared a boundary that
   excluded only §5/§6 while §1-§4 narrative prose was also unpinned; the sixth
   clause-scoped that abstract but left its HEADLINE sentence — where all three
   visibility COUNTS are stated — unpinned, so `one of the merged levers is visible to a
   user, one is not,` could become `all of the merged levers are visible to a user,` with
   every pin green, and left §4's verdict check PREFIX-scoped while its message claimed a
   whole-cell guarantee.
   **A claim wider than its measurement is the defect this file exists to avoid**, so when
   adding a row here, mutate the cell first — and mutate a digit upward, which is the
   bump a substring check misses.

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


def test_the_artifact_carries_the_pin_identity_that_makes_it_reproducible(artifact, pin):
    rec = artifact["pin"]
    assert rec["schema"] == cap.PIN_SCHEMA
    assert rec["digest"] and rec["query_digest"] and rec["sha256"]
    # ⛔ EQUALITY, not presence. The README's headline is `sha256 2486a359…,
    # pinned in the artifact`, and "pinned" has to mean THIS file: a presence
    # check passes any digest at all (a foreign sha256 stayed green before this
    # line), which is the difference between an identity and a field being set.
    assert rec["sha256"] == hashlib.sha256(PIN.read_bytes()).hexdigest()
    assert rec["queries"] == len(json.loads(PIN.read_text())["query_ids"])
    # ⛔ The OTHER three identities the record publishes were left on the presence
    # check above, and a presence check is not an identity: a mutation probe
    # (`rec["digest"] -> 'deadbeefdeadbeef'` with the README edited to agree) stayed
    # GREEN, so the artifact could publish a digest that contradicts the very file
    # the suite has already recomputed one function up. The committed pin file is the
    # authority for all three — compare against ITS values, never against themselves.
    assert rec["digest"] == pin["digest"], (
        "the artifact's `pin.digest` must be the pin file's own recomputed digest "
        "(`test_the_pin_digest_recomputes_from_its_own_rows` is the authority)")
    assert rec["query_digest"] == pin["query_digest"], (
        "the artifact's `pin.query_digest` must be the pin file's own recomputed "
        "query digest")
    assert rec["shares"] == pin["rules"]["shares"], (
        f"the artifact publishes class shares {rec['shares']!r} that are not the "
        f"pin file's {pin['rules']['shares']!r} — the population is re-identifiable "
        f"only if these are the same partition")
    assert rec["population"] == len(pin["population_rows"])
    assert rec["queries"] == len(pin["query_ids"])
    assert rec["path"] == "docs/evidence/12186-capstone/queries.json"


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


def test_the_user_door_note_states_the_arguments_the_doors_do_not_default_to(artifact):
    """The note must not call this fixture's arguments "the tool's own defaults".

    ⛔ MEASURED DEFECT this pins. The note read

        sdk.tortoise_fts_query(entity_type='object') at the MCP tool
        `tortoise_search`'s own defaults — the USER door (REST GET /v1/search)

    which is false about TWO default values, and it sat under the README row
    labelled "user door — SHIPPED (what a user gets today)". The doors' own
    defaults are `entity_type='point'` and `limit=10`
    (`tortoise/mcp_server.py::tortoise_search`); the arms call
    `entity_type='object'` (the population IS `:Object` names) at `limit=KMAX`=15.

    The VERB really is the user door's — both doors resolve to this SDK method,
    which is what `test_the_artifact_declares_the_door_it_measured` checks. The
    ARGUMENTS are not. `_user_door_binding` compares only `sdk_method`, so nothing
    could catch an argument claim; this is that check.
    """
    import inspect

    from tortoise import mcp_server

    sig = inspect.signature(mcp_server.tortoise_search)
    door_entity = sig.parameters["entity_type"].default
    door_limit = sig.parameters["limit"].default

    note = cap.DOOR_NOTES["user_search"]

    # The divergence is REAL, and this is the control: if a future change made
    # either door default to what the arms pass, the note would be wrong the other
    # way and must be re-stated. A note asserting a difference that no longer
    # exists is the same defect mirrored.
    assert door_entity != "object", (
        f"the MCP tool now defaults `entity_type` to {door_entity!r}, which is what "
        f"the arms pass — DOOR_NOTES['user_search']'s verb/argument split is stale")
    assert door_limit != cap.KMAX, (
        f"the MCP tool now defaults `limit` to {door_limit!r} == KMAX, so the "
        f"\"limit=15 is not a shipped window\" caveat is stale")

    # …and the note says so, in the terms a reader needs.
    assert f"entity_type='object'" in note, (
        f"the note must state the `entity_type` the arms actually pass: {note!r}")
    assert f"limit={cap.KMAX}" in note, (
        f"the note must state the `limit` the arms actually pass: {note!r}")
    assert f"default to '{door_entity}'" in note, (
        f"the note must name the doors' real `entity_type` default ({door_entity!r}) "
        f"so the reader can see the divergence: {note!r}")
    assert f"default to {door_limit}" in note, (
        f"the note must name the doors' real `limit` default ({door_limit!r}): {note!r}")
    # The exact falsehood, asserted ABSENT — the phrase is what made two defaults
    # wrong while the sentence read as a checked fact.
    assert "own defaults" not in note, (
        "the note claims the tool's \"own defaults\" while diverging from two of "
        f"them ({door_entity!r}, {door_limit!r}): {note!r}")

    # The COMMITTED record must carry the same prose — the artifact is the evidence
    # a reader opens, and a corrected constant beside a stale artifact is the same
    # claim/evidence split this file exists to close.
    for arm in ("user_search", "user_search_pre_lever", "user_search_rerank"):
        assert artifact["arms"][arm]["door_note"] == note, (
            f"the committed artifact's `{arm}.door_note` is not the tool's current "
            f"note — the record would keep publishing the corrected-away sentence")


def test_the_artifact_names_a_reachable_head_to_run_its_commands_against(artifact):
    """`code_head` is truthful but UNREACHABLE, so the record must name a stand-in.

    ⛔ MEASURED DEFECT this pins. `code_head` is the run head `31bce52ddb`, which
    predates the rebase onto `origin/main`; the rebase REWROTE that sha, so it is an
    ancestor of no ref on the published branch:

        $ git merge-base --is-ancestor 31bce52ddb HEAD   -> exit 1
        $ git branch -a --contains 31bce52ddb           -> (empty)
        $ git show 31bce52ddb:tools/capstone_12186_recall.py
        fatal: invalid object name '31bce52ddb'.

    README §6 and this module both present that `git show` as the evidence for the
    `cleanup` hand-edit, so the record's reproduction anchor resolved only on the
    machine that held the pre-rebase lane ref. Re-anchoring `code_head` itself would
    be a lie about which run this is; naming a reachable commit whose INPUTS are the
    same blobs is the fix that needs no re-run.
    """
    note = artifact["code_head_note"]
    assert note and isinstance(note, str), (
        "`code_head_note` must be present: `code_head` alone is a dead anchor after "
        "a rebase rewrites it")
    assert artifact["code_head"][:12] in note, (
        "the note must name the run head it is explaining")
    # An alternative commit a reader can actually resolve, distinct from the run head.
    # 10 hex, not 12: a short sha is what a reader is handed (`318950c6e0`), and a
    # `\b`-bounded 10-run will not match inside a longer sha.
    import re

    shas = set(re.findall(r"\b[0-9a-f]{10}\b", note))
    alternatives = {s for s in shas
                    if not artifact["code_head"].startswith(s)}
    assert alternatives, (
        f"the note names no reachable alternative commit, so it still leaves the "
        f"reader with a sha they cannot resolve: {note!r}")
    # The README must carry the same alternative: a corrected artifact beside a
    # README still handing out the dead sha is the claim/evidence split again.
    readme = README.read_text()
    assert any(s in readme for s in alternatives), (
        f"README carries none of the alternative commits {sorted(alternatives)!r} "
        f"that `code_head_note` offers, so the prose still yields an unresolvable "
        f"`git show`")
    # And the reproduction command the record documents must be the reachable form.
    assert any(f"{s}:tools/capstone_12186_recall.py" in readme for s in alternatives), (
        "README §6's reproduction command must run against the reachable commit, "
        "not the rebased-away run head")


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


def test_run_refuses_a_binding_that_is_not_the_users_door(monkeypatch, capsys):
    """`run()` must not write an artifact it cannot back with the binding.

    ⛔ THE REASON IS ASSERTED, NOT ONLY THE EXIT CODE. `run()` has four `return 3`
    arms and the `TORTOISE_DB_URI` guard sits a few lines below the door guard and
    returns 3 too, so an exit-code-only assertion is satisfied by the very mutant
    this test exists to catch — measured: with `if not user_door.get(
    "matches_measured_call")` deleted, the test still passed (`rc=3`, the URI arm
    answering). That matters HERE specifically because the manifest declares this
    file hermetic (`config/ci-surfaces.yml`: "no DB, no graph, no docker, no
    network, no model call"), which is exactly the environment where the URI arm can
    answer for the door arm — so the environment is pinned EMPTY as well.
    """
    import argparse
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    monkeypatch.setattr(cap, "_user_door_binding",
                        lambda: {"tool": "tortoise_search", "present": False})
    args = argparse.Namespace(pin=str(PIN), graph="cap-test", namespace=None,
                              out=None)
    assert cap.run(args) == 3
    err = capsys.readouterr().err
    assert "does not resolve to the call the arms make" in err, (
        f"`run()` returned 3 for a reason other than the door guard — the refusal "
        f"that fired was not the one this test names: {err!r}")
    assert "no live FalkorDB" not in err, (
        f"the URI guard answered instead of the door guard, so deleting the door "
        f"guard would leave this test green: {err!r}")


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


def test_the_artifact_declares_the_edits_made_after_its_run(artifact):
    """`code_head` is the record's reproduction anchor, so a hand edit is DECLARED.

    The committed record is not entirely the output of the run it names: the
    `cleanup` block was inserted by hand, and the tool AT `code_head` has no
    `cleanup` builder at all —

        $ git show 31bce52ddb:tools/capstone_12186_recall.py | grep -c cleanup
        0

    — so a reader following `code_head` + README §6 reproduces an artifact WITHOUT
    that block, while `test_the_artifact_declares_the_graph_residue` pins the
    committed block as if it were run output. No figure is affected (every figure is
    run output), which is why this is provenance and not a measurement defect; but a
    record that cannot say which of its blocks its own head emits is not re-derivable.

    Declaring it is the fix that needs no re-run: the run's own builder writes
    `post_run_edits: []`, so a later run states "none" instead of omitting the
    question, and an edit is bound to the head it edits rather than to a free-text
    note somewhere else.
    """
    edits = artifact["post_run_edits"]
    assert isinstance(edits, list), (
        f"`post_run_edits` is {edits!r}, not a list: the field must be present on "
        f"every record, `[]` included, so an omitted field never reads as 'no edits'")
    assert edits, (
        "no post-run edits are declared, but this record carries a `cleanup` block "
        "its `code_head` cannot emit (`git show "
        "31bce52ddb:tools/capstone_12186_recall.py | grep -c cleanup` = 0). If you "
        "re-ran the measurement and YOUR run emitted the block, then — like every "
        "other pin in this file — this is a claim about the committed record: re-cut "
        "it against the new artifact")
    for edit in edits:
        assert edit["field"] in artifact, (
            f"a declared post-run edit names {edit['field']!r}, which the record "
            f"does not carry: {edit!r}")
        assert edit["run_head"] == artifact["code_head"], (
            f"the edit's `run_head` {edit['run_head']!r} is not the `code_head` "
            f"{artifact['code_head']!r} it modifies — a declaration that is not "
            f"pinned to the run it edits records nothing about the head it claims")
        assert edit["why"].strip(), f"a declared edit states no reason: {edit!r}"
    assert "cleanup" in {e["field"] for e in edits}, (
        f"the `cleanup` block is the hand-inserted one (see this docstring's "
        f"`git show`: the tool at `code_head` has no such builder), so it must be "
        f"declared here; declared edits are {[e['field'] for e in edits]}")


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


def test_the_two_restated_prose_figures_are_pinned_and_not_only_the_tables(artifact):
    """The rows were pinned; the PROSE beside them was not — and it had regressed.

    ⛔ MEASURED DEFECT this pins. README §2a's commentary says "14 of its 300 windows
    were keyword-only and 8 queries returned nothing at all — its OWN row in the table
    above, not the shipped arm's 18/6". That sentence IS the previous round's P2 (it
    had given the rerank arm the shipped arm's 18/6), and the correction was left
    UNPINNED: the table row one line above is cell-checked by
    `test_the_readme_quotes_the_leg_degradation_the_artifact_holds`, so reverting the
    prose to 18/6 — or to any other pair — stayed GREEN.

    §5's "The 53.3 % mint rate is therefore an upper bound" is the same shape, and was
    not even in the declared NOT-PINNED enumeration: a THIRD restatement of a
    tabulated rate (after §3b and §4) able to contradict both with a green suite.

    Both are DERIVED here, never hard-coded, so the prose cannot drift from the record.
    """
    text = README.read_text()
    n_queries = artifact["pin"]["queries"]

    rerank = artifact["arms"]["user_search_rerank"]
    shipped = artifact["arms"]["user_search"]
    r_kw = rerank["window_modes"]["keyword_only"]
    r_zero = rerank["rows_zero"]["3"]
    s_kw = shipped["window_modes"]["keyword_only"]
    s_zero = shipped["rows_zero"]["3"]

    # §2a — the rerank arm's OWN pair…
    own = (f"{r_kw} of its {n_queries} windows were keyword-only and "
           f"{r_zero} queries returned nothing at all")
    assert own in text, (
        f"README §2a must quote the rerank arm's own degradation pair {own!r} "
        f"(artifact holds {r_kw} keyword-only / {r_zero} empty, NOT the shipped "
        f"arm's {s_kw}/{s_zero}) — this is the exact figure that regressed once")
    # …and it must say which arm's pair it is NOT, so a reader cannot reattribute it.
    not_shipped = f"not the shipped arm's {s_kw}/{s_zero}"
    assert not_shipped in text, (
        f"README §2a must name the shipped arm's pair ({not_shipped!r}) as the one "
        f"this sentence is NOT quoting; otherwise the two pairs are indistinguishable")

    # §5 — the mint rate restated as an upper bound, derived from the record.
    rate = f"{artifact['arms']['mint_rate']['shipped']['rate'] * 100:.1f} %"
    assert f"The {rate} mint rate is therefore an" in text, (
        f"README §5 must restate the mint rate as {rate!r} (the artifact's 
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
        #: A `<` BOUND is pinned EXACTLY, not by inequality: `< 1e-12` -> `< 0.99`
        #: stays TRUE (`9.09e-13 < 0.99`), so an inequality check called it green while
        #: §2 published a far weaker significance bound than the artifact measured —
        #: the same figure §4 pins exactly (measured). A plain number stays a float
        #: comparison, because the doc legitimately renders `1` as `1.0`.
        if p.startswith("<"):
            assert p.replace(" ", "") == cell["p_display"].replace(" ", ""), (
                f"README p bound for {name} is {p!r}, artifact records "
                f"{cell['p_display']!r}")
            assert cell["p"] < float(p.replace("<", "").strip()), (name, p)
        else:
            assert float(p) == pytest.approx(cell["p"])


def _clause(paragraph: str, marker: str) -> str:
    """The SENTENCE of `paragraph` that mentions `marker`, flattened.

    The abstract states three verdicts in three sentences, and a token pinned only
    against the WHOLE paragraph is satisfied by any of them: `0lost` occurs in the
    rerank clause, so `41 queries gained, none lost` -> `41 queries gained, 9 lost`
    in the underscore clause stayed green (measured). Clause-scoping is what makes a
    figure belong to the lever it is stated about.

    ⛔ Two hazards this handles, both measured:
    - a naive `split(". ")` breaks on an abbreviation (`i.e.`, `e.g.`, `vs.`, `etc.`),
      turning a correct clause into an "omits 83.4%" red — a false red is as bad as a
      false pass;
    - a rewording that MERGES two clauses would let one sentence satisfy two lever
      markers, silently destroying the attribution this helper exists to provide.
      So the selected sentence may mention no OTHER lever.
    """
    sentences = [s for s in re.split(r"(?<!\b[A-Za-z])\.\s+", paragraph)
                 if marker in s]
    others = [m for m in ("#12368", "#12343", "#12304") if m != marker]
    assert not any(m in sentences[0] for m in others), (
        f"the sentence for {marker!r} also mentions another lever, so one sentence "
        f"is satisfying two markers: {sentences[0]!r}")
    return _flat(_clause_raw(paragraph, marker))


def _clause_raw(paragraph: str, marker: str) -> str:
    """The RAW sentence of `paragraph` that mentions `marker` — `_clause` unflattened.

    Kept beside the flattened form because a WORD-shaped pin cannot use a flattened
    one: `_flat` strips the spaces, so `noneofthemergedleversisvisibletoauser`
    CONTAINS `oneofthemergedleversisvisibletoauser` and `one` -> `none` — a full
    inversion of the headline's claim — stays green through any `_mentions` check.
    Sentence selection is `_clause`'s (abbreviation-safe split, one marker per
    sentence), reused rather than re-implemented.
    """
    sentences = [s for s in re.split(r"(?<!\b[A-Za-z])\.\s+", paragraph)
                 if marker in s]
    assert len(sentences) == 1, (
        f"expected exactly one sentence mentioning {marker!r}, got "
        f"{len(sentences)} in {paragraph!r}")
    return sentences[0]


#: The headline sentence's three visibility CLASSES, each with the shape in which it
#: states its count. Matched on the RAW sentence, where `\b` survives and the counted
#: word cannot be swallowed by a longer one.
_HEADLINE_CLAUSES: dict[str, str] = {
    "visible to a user": r"\b(\w+) of the merged levers (?:is|are) visible to a user\b",
    "not visible to a user": r"\b(\w+) (?:is|are) not\b",
    "only visible on the write path":
        r"\b(\w+) (?:is|are) only visible on the write path\b",
}

#: The count words the headline may spell a class count with. Digits are absent on
#: purpose: the sentence states small counts in words, and accepting either would make
#: "which spelling does this pin accept" a second, unpinned question.
_COUNT_WORDS: dict[str, int] = {"none": 0, "one": 1, "two": 2, "three": 3,
                                "four": 4, "five": 5}


def _door_verdict_counts(artifact) -> dict[str, int]:
    """How many measured levers the artifact puts in each visibility class.

    Derived, never typed: the three classes are the artifact's own three blocks —
    `#12368` measured at the USER's door, `#12343` measured only with the rerank
    `_if_armed` (an ARMED figure, so it is a user-visible lever only if it also SHIPS
    on AND the run saw it in user search), and `#12304` measured only on the WRITE
    path (`arms.mint_rate`, `§3b`). A field that disappears fails the assertion rather
    than silently reclassifying a lever.
    """
    visibility = artifact["merged_lever_visibility"]
    mint = artifact["arms"]["mint_rate"]
    assert set((visibility["underscore_terms_shipped_default"],
                visibility["fused_rerank_shipped_default"])) <= {"ON", "OFF"}, (
        f"`merged_lever_visibility` no longer holds ON/OFF shipped defaults: "
        f"{visibility}")
    ran_rerank = visibility["fused_rerank_ran_in_user_search"]
    assert ran_rerank in (True, False), (
        f"`fused_rerank_ran_in_user_search` is {ran_rerank!r}, not a boolean, so the "
        f"rerank's class cannot be derived from it")
    assert mint["shipped"]["attached"] > mint["norm_only"]["attached"], (
        f"the artifact no longer records the shipped matcher attaching write-path "
        f"variants the frozen norm-only matcher mints "
        f"({mint['shipped']['attached']} vs {mint['norm_only']['attached']}), so the "
        f"headline's write-path class has no premise")
    classes = {
        "#12368": ("visible to a user"
                   if visibility["underscore_terms_shipped_default"] == "ON"
                   else "not visible to a user"),
        "#12343": ("visible to a user"
                   if (visibility["fused_rerank_shipped_default"] == "ON"
                       and ran_rerank is True)
                   else "not visible to a user"),
        "#12304": "only visible on the write path",
    }
    counts = {name: 0 for name in _HEADLINE_CLAUSES}
    for verdict in classes.values():
        counts[verdict] += 1
    assert sum(counts.values()) == len(classes), (
        f"a lever was classified into no headline class: {classes}")
    return counts


def _assert_headline_states(sentence: str, counts: dict[str, int]) -> None:
    """The headline sentence states the class COUNTS the artifact supports.

    ⛔ Its BOUNDARY, stated so the claim is not wider than what it enforces: this pins
    the three COUNTS and the class each is stated for. The wording around them is
    free — a reworded clause that keeps the counts passes, deliberately, because the
    counts are the claim and the phrasing is not evidence.
    """
    for name, pattern in _HEADLINE_CLAUSES.items():
        match = re.search(pattern, sentence)
        assert match, (
            f"the abstract's headline no longer states the {name!r} count "
            f"({counts[name]} lever(s)) — a visibility class cannot be dropped or "
            f"merged into another: {sentence!r}")
        word = match.group(1).lower()
        assert word in _COUNT_WORDS, (
            f"the headline's {name!r} count reads {word!r}, which is not a count the "
            f"artifact can support (it holds {counts[name]}): {sentence!r}")
        assert _COUNT_WORDS[word] == counts[name], (
            f"the headline says {word!r} lever(s) are {name!r}; the artifact holds "
            f"{counts[name]}: {sentence!r}")


def _paragraph(text: str, marker: str) -> str:
    """The blank-line-delimited BLOCK of `text` that mentions `marker`, flattened.

    A whole-document substring is the shape this file's header forbids: the refusal
    pin used `"0.7133" in text`, and the baseline occurs in three places, so the
    refusal paragraph could lose its own copy while the check stayed green
    (measured). Block-scoping is the smallest honest unit for prose that is itself a
    paragraph.
    """
    blocks = [b for b in text.split("\n\n") if marker in " ".join(b.split())]
    assert len(blocks) == 1, (
        f"expected exactly one paragraph mentioning {marker!r}, got "
        f"{len(blocks)}")
    return _flat(blocks[0])


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
#: test) and is asserted to be a real ON/OFF word before use, so the check can never
#: go vacuous on an empty prefix.
#:
#: ⛔ The verdict prefix is the row's whole LEADING PHRASE, not its first word. A
#: one-word check passes on a sentence that reverses itself: `n/a — no code shipped`
#: -> `n/a — code shipped`, `a user does not get it` -> `a user gets it` and
#: `**YES — +15.47 pts**` -> `**YES, only for admins — +15.47 pts**` all survived a
#: `startswith("YES"/"n/a"/...)` check (measured). The phrase ends where the row's
#: first FIGURE begins, which is the point beyond which no wording is claimed.
LEVER_COLUMNS: dict[str, tuple[str, str]] = {
    "underscore": ("", "YES—"),
    "fold": ("ON", "YESonthewritepath"),
    "rerank": ("", "NO—auserdoesnotgetit"),
    "verdict": ("ON", "notmeasurablehere"),
    "m3": ("measured", "n/a—nocodeshipped"),
}

#: The rows whose verdict is a DECLARATION rather than a measurement: their cell
#: must not claim visibility, in any wording.
LEVER_DECLARATION_ROWS = ("verdict", "m3")


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
    #: ⛔ A derived prefix that is empty makes `startswith` vacuously true. Assert the
    #: artifact still records a real ON/OFF word before it is used as one (measured:
    #: an empty value let `**ON**` -> `**OFF**` through).
    assert set(derived_shipped.values()) <= {"ON", "OFF"}, (
        f"`merged_lever_visibility` no longer holds ON/OFF shipped defaults: "
        f"{derived_shipped}")
    assert visibility["fused_rerank_ran_in_user_search"] is False, (
        "the artifact no longer records the rerank as un-run in user search, so "
        "§4's `NO` verdict has no premise")
    for key, marks in LEVER_ROW_MARKERS.items():
        row = _one_row(rows, marks, key)
        shipped = _flat(row[1])
        verdict = _flat(row[2])
        want_shipped, want_verdict = LEVER_COLUMNS[key]
        want_shipped = derived_shipped.get(key, want_shipped)
        assert want_shipped, f"§4 `{key}` has no shipped-state expectation"
        assert shipped.startswith(want_shipped), (
            f"§4 `{key}` shipped state {row[1]!r} does not start with "
            f"{want_shipped!r}")
        assert want_verdict, f"§4 `{key}` has no verdict expectation"
        #: ⛔ PREFIX-SCOPED, and the claim is now no wider than that. A `startswith`
        #: bounds the row's LEADING PHRASE only — text appended AFTER it is not
        #: constrained, and no `startswith` can pretend otherwise (measured:
        #: `**YES on the write path** —` -> `**YES on the write path, but only for
        #: admins** —` stayed green, and so did `ON (capture, LLM)` ->
        #: `ON (capture)`). The row whose wording IS the artifact's is pinned WHOLE
        #: below, which is where a qualifier inserted into a live verdict reds; the
        #: rows whose wording the artifact does not hold are bounded by this prefix
        #: and by the phrase check, and are not claimed to be more than that.
        assert verdict.startswith(want_verdict), (
            f"§4 `{key}` verdict {row[2]!r} does not start with {want_verdict!r} "
            "— this bounds the row's LEADING PHRASE only (the phrase ends where "
            "the row's first figure begins); wording appended after it is pinned "
            "where the artifact derives the whole cell")
        if key in LEVER_DECLARATION_ROWS:
            #: Reject only an UN-NEGATED visibility claim. `"visible" not in verdict`
            #: reds a strictly more correct negation (`… so it is not visible to a
            #: user`) — a false red — while the phrase prefix above already carries the
            #: fail-closed part (measured).
            assert not re.search(r"(?<!not )(?<!no )\bvisible\b", verdict), (
                f"§4 `{key}` is a DECLARATION row but its verdict now claims "
                f"visibility: {row[2]!r}")

    #: ⛔ WHOLE-CELL, because this row's wording IS a fact the artifact holds: the
    #: write-path surface (the mint-rate arm, `§3b`) plus the two rates the shipped
    #: and norm-only matchers produce, and nothing else is claimed in the cell. A
    #: `startswith` cannot see a qualification inserted after the phrase — measured:
    #: `**YES on the write path** — mint rate 1.0000 → 0.5328` with
    #: `, but only for admins` spliced in stayed GREEN — so the cell is pinned whole
    #: here, and the prefix loop above is left to the rows whose wording is prose.
    fold = _one_row(rows, LEVER_ROW_MARKERS["fold"], "fold")
    fold_mint = artifact["arms"]["mint_rate"]
    assert _flat(fold[1]) == "ON(writepath)", (
        f"§4 `#12304` shipped state {fold[1]!r} is not `ON (write path)`. Both halves "
        f"are derived: the shipped matcher attaches variant pairs the frozen "
        f"norm-only matcher mints (`arms.mint_rate`, {fold_mint['shipped']['attached']} "
        f"vs {fold_mint['norm_only']['attached']}), and the write path is the ONLY "
        f"surface the artifact measures it on — a cell that drops the surface reads "
        f"as a user-door lever")
    assert _flat(fold[2]) == (
        f"YESonthewritepath\u2014mintrate{fold_mint['norm_only']['rate']:.4f}"
        f"\u2192{fold_mint['shipped']['rate']:.4f}"), (
        f"§4 `#12304` verdict {fold[2]!r} is not the derived write-path verdict "
        f"(leading phrase + the two mint rates the artifact holds). This row is "
        f"pinned WHOLE because the artifact derives all of it, so ANY inserted "
        f"qualification is a changed claim, not a rewording")

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
    paragraph = " ".join(paragraph)
    assert paragraph.strip(), "the plain-language summary is empty"

    visibility = artifact["merged_lever_visibility"]
    underscore = visibility["underscore_terms_visible_on_user_door"]
    rerank = visibility["fused_rerank_effect_on_user_door_if_armed"]
    mint = artifact["arms"]["mint_rate"]

    #: CLAUSE-SCOPED — see `_clause`. `none lost` is how the doc spells 0 here, and
    #: the significance bound is the figure the whole `+15.5 points` claim rests on:
    #: neither the loss count nor `p < 1e-12` nor the `top 3` was pinned before, and
    #: all three were editable with every pin green (measured: `none lost` ->
    #: `9 lost`, `p < 1e-12` -> `p < 0.4`, `top 3` -> `top 15`).
    _assert_mentions(
        _clause(paragraph, "#12368"),
        (f"{underscore['before_recall_clean'] * 100:.1f}%",
         f"{underscore['after_recall_clean'] * 100:.1f}%",
         f"top{underscore['k']}",
         f"+{underscore['delta_pts_clean']:.1f}points",
         f"{underscore['gains']}queriesgained",
         "nonelost" if underscore["losses"] == 0
         else f"{underscore['losses']}lost",
         f"p{underscore['p_display']}"),
        "the abstract's `#12368` clause")

    _assert_mentions(
        _clause(paragraph, "#12343"),
        (f"+{rerank['delta_pts_clean']:.2f}points",
         f"{rerank['gains']}gained",
         f"{rerank['losses']}lost",
         f"p={rerank['p_display']}",
         #: The CAPABILITY statement, derived from the artifact — without these the
         #: abstract's conclusion was invertible: `not visible to a user, because it
         #: is off by default` -> `visible … on by default` stayed green (measured).
         f"{visibility['fused_rerank_shipped_default'].lower()}bydefault",
         "notvisibletoauser"),
        "the abstract's rerank clause")

    #: The three-lever summary sentence. Its counts are DECLARED here and tied to the
    #: §4 verdicts immediately below, so `one … is visible` -> `two … are visible`
    #: cannot pass while §4 still says one YES-user, one NO and one write-path.
    for key, want in (("underscore", "YES"), ("rerank", "NO"),
                      ("fold", "YESonthewritepath")):
        assert LEVER_COLUMNS[key][1].startswith(want), (
            f"the abstract's three-lever sentence no longer matches §4's `{key}` "
            f"verdict {LEVER_COLUMNS[key][1]!r}")
    #: ⛔ The COUNTS are derived from the artifact and matched on the RAW sentence.
    #: The literal tokens below are necessary but not sufficient: `_flat` deletes the
    #: spaces, so `noneofthemergedleversisvisibletoauser` CONTAINS
    #: `oneofthemergedleversisvisibletoauser` — the whole claim inverted through the
    #: flattening — while a word-boundary match on the raw sentence cannot be fooled.
    #: Each class's count is read from the artifact (`_door_verdict_counts`), so the
    #: pin cannot drift with the document: `one … is visible, one is not, and one is
    #: only visible on the write path` is what the record holds, not what the last cut
    #: of this test typed.
    _assert_headline_states(_clause_raw(paragraph, "Plain language"),
                            _door_verdict_counts(artifact))

    _assert_mentions(
        _flat(paragraph),
        ("oneofthemergedleversisvisibletoauser",
         "oneisnot",
         "oneisonlyvisibleonthewritepath"),
        "the abstract's three-lever sentence")

    _assert_mentions(
        _clause(paragraph, "#12304"),
        (f"{mint['norm_only']['rate'] * 100:.0f}%",
         f"{mint['shipped']['rate'] * 100:.1f}%"),
        "the abstract's `#12304` clause")


def test_the_readme_refuses_the_cross_corpus_comparison_too(artifact):
    """The artifact refuses it; the REFUSAL paragraph must not invite it either.

    This used to be `"0.7133" in text` over the whole 226-line document — the shape
    the header above forbids. The baseline occurs in three places, so the refusal
    paragraph could lose its own copy while the check stayed green (measured).
    """
    block = _paragraph(README.read_text(), "refuses that comparison")
    assert "0.7133" in block, f"the refusal paragraph no longer names 0.7133: {block!r}"
    assert ("refusesthatcomparison" in block
            or "notacomparisontothe0.7133" in block), block


def test_the_readme_narrative_cannot_contradict_its_own_tables(artifact):
    """The three §2/§3 NARRATIVE restatements of a tabulated figure.

    These are the prose places that repeat a number the tables already hold, and
    each survived unpinned (measured): `+53.3 points at k=3` -> `+43.3` contradicted
    the per-class table two lines above it; the near-duplicate rule's
    `Jaccard >= 0.8` -> `0.5` contradicted `cap.NEARDUP_JACCARD` IN A PARAGRAPH THAT
    SAYS IT IS PINNED BY THIS FILE; and `192 of 411` -> `291 of 411` contradicted
    §3b's own row.

    Whole-document and digit-bounded, deliberately: each token is a two-number phrase
    or a comparison operator that occurs exactly once, so the masking a bare
    substring allows (a second occurrence satisfying the pin) is closed by the
    uniqueness assertion, and the tables carrying the same figures are cell-pinned.
    """
    text = _flat(README.read_text())
    ident = artifact["arms"]["user_search"]["by_class"]["ident"]
    pre = artifact["arms"]["user_search_pre_lever"]["by_class"]["ident"]
    mint = artifact["arms"]["mint_rate"]
    tokens = (f"{(ident['3'] - pre['3']) * 100:.1f}points",
              f"\u2265{cap.NEARDUP_JACCARD}",
              f"{mint['shipped']['attached']}of{mint['pairs']}")
    for token in tokens:
        assert text.count(token) == 1, (
            f"expected exactly one occurrence of {token!r} in the README, found "
            f"{text.count(token)} — a second occurrence can mask a drifted first")
    _assert_mentions(text, tokens, "the README's §2/§3 narrative")
