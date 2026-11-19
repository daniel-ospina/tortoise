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

   NOT PINNED, and the list is deliberate: narrative prose elsewhere in §1-§4 —
   `~2.3 %` is now PINNED (see below), so the remaining unpinned set is
   `+1.7 and +6.7 points`, `0 gains, 0 losses across 272`, `282/300`, `34 of 300` —
   plus §5's declared LIMITS (items 1-4: not-a-census, phase 2 unmeasured, the
   load confound, the dense-leg note), item 5's EXTERNAL production share (no
   artifact holds it), and §6's run metadata. ⛔ §5's items 5-7 are NOT in that
   exclusion: their figures (the `30 % / 10 %` oversampling, `n = 300`, the
   `±0.33 pts` quantum, `35 of 300` excluded) are derived from the record by
   `test_section_fives_numbered_limits_five_to_seven_state_the_figures_the_artifact_
   holds`, and the previous boundary said only "items 1-4", which a reader correctly
   read as "5-7 are covered" while no assertion reached them (measured: all three
   stayed green through a mutation). ⛔ §5's DOOR paragraph is
   NOT in that exclusion either: it is asserted at `test_the_user_door_note_states_the_
   arguments_the_doors_do_not_default_to`. A reader who needs a figure guarded must
   check it is in a pinned table; do NOT read this module as "the document is
   enforced".

   ⛔ THREE figures LEFT this list because leaving them unpinned was measured
   exploitable: §2a's `14 … / 8` prose (the prior round's own P2 — corrected, then
   revertible to `18/6` with a green suite), §5's `53.3 %` mint rate (a THIRD
   restatement of a tabulated rate, absent from this enumeration entirely), and §3a's
   `~2.3 %` / `1.4 %` pair (which also had to be RELABELLED — 2.3 % is the k=3 cell,
   but 3 is the capture prior's window, not the user door's, which is 10). All three
   are derived and asserted by
   `test_the_two_restated_prose_figures_are_pinned_and_not_only_the_tables`.

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

import ast
import hashlib
import inspect
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


def _committed_json(path: Path) -> dict:
    """A COMMITTED input, read — and a MISSING one is a FAILURE, never a skip.

    ⛔ MEASURED FAIL-OPEN (round 18, F1). Both fixtures below used `pytest.skip` when
    their path did not exist, under a comment that read "skipped until the run
    lands" — true while this PR was being developed, FALSE the moment the record was
    committed by it. With `measurement.json` removed and nothing else changed, **26 of
    the 48 pins turned into SKIPS** and the suite reported `22 passed, 26 skipped`,
    exit 0: a lost evidence record (a bad merge, a re-cut that drops the file, a docs
    move) would take every figure pin with it and still read green.

    That is the shape this file's own docstring rules out two paragraphs down ("a
    gate that runs only where it cannot answer is a no-op gate"), so the record is
    treated as what it is: an input of this branch, present in every checkout, whose
    absence means the evidence was lost.
    """
    assert path.exists(), (
        f"the committed record {path} is MISSING. It is an input of this branch, not "
        f"an optional lane: its absence means the evidence was lost, and every figure "
        f"pin in this file would otherwise skip and report the suite green")
    return json.loads(path.read_text())


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
    return _committed_json(PIN)


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


def test_a_missing_committed_record_is_a_failure_and_not_a_skip(tmp_path):
    """F1's own pin: the reader's refusal, asserted rather than trusted by inspection.

    ⛔ A SKIP IS NOT A FAILURE, AND `pytest.raises(AssertionError)` CANNOT SEE THE
    DIFFERENCE (measured): restoring the old `pytest.skip(...)` on the absent path made
    this test SKIP rather than red, and the suite still exited 0 at
    `48 passed, 1 skipped`. The mutant is precisely the 26-pin fail-open, so the pin
    asserts the CLASS of the exit — anything that is not an AssertionError (a skip, a
    `return {}`, no raise at all) fails here.
    """
    absent = tmp_path / "absent.json"
    try:
        _committed_json(absent)
    except AssertionError as exc:
        assert "is MISSING" in str(exc), exc
    except BaseException as exc:  # the FAIL-OPEN arms: a skip, a SystemExit, anything else
        pytest.fail(
            f"a missing committed record raised {type(exc).__name__} instead of failing "
            f"the suite — `pytest.skip` is the fail-open this pins (26 figure pins "
            f"turned into skips and the suite reported green)")
    else:
        pytest.fail(
            "a missing committed record returned instead of failing the suite — every "
            "figure pin in this file would then assert against an empty dict")
    present = tmp_path / "present.json"
    present.write_text('{"ok": true}')
    assert _committed_json(present) == {"ok": True}


def test_neither_committed_record_fixture_can_skip():
    """⛔ THE PIN ABOVE PINS THE HELPER, NOT THE WIRING (round 19, measured).

    Restoring the removed two lines INSIDE the fixture — `if not ARTIFACT.exists():
    pytest.skip(...)` followed by `return _committed_json(ARTIFACT)` — puts the
    original P1 fail-open back in full while `_committed_json` itself stays correct
    and its pin above still passes: measured, `23 passed, 26 skipped`, exit 0.

    No assertion that READS a fixture can see this, because a skip removes the test
    that would assert: the check has to be about the fixture's own source. That is
    what this is — a derived constraint on the two functions, not a restatement of
    them: the ONLY exit either may take on a missing record is the helper's failure.
    """
    for fn, path in ((pin, PIN), (artifact, ARTIFACT)):
        src = inspect.getsource(fn)
        assert "skip" not in src, (
            f"the `{path.name}` fixture may not skip: a fixture that skips takes every "
            f"test that requests it with it, so a missing committed record reads as a "
            f"green suite (measured: 23 passed, 26 skipped, exit 0). A missing record "
            f"must FAIL through `_committed_json`. Fixture source: {src!r}")
        assert "_committed_json" in src, (
            f"the `{path.name}` fixture must read the record THROUGH `_committed_json`, "
            f"which is the only path that fails on a missing file. Source: {src!r}")


# ── the artifact (committed by this branch — see `_committed_json`) ───────────

@pytest.fixture(scope="module")
def artifact() -> dict:
    return _committed_json(ARTIFACT)


def _arm_names() -> list[str]:
    return [arm for arm, _door, _env in cap.ARMS]


def test_the_artifact_measures_every_arm_the_capstone_quotes(artifact):
    for arm in _arm_names():
        assert arm in artifact["arms"], arm
    assert "mint_rate" in artifact["arms"]
    assert artifact["ks"] == list(cap.KS)


def test_the_mint_arm_is_measured_inside_the_environment_guard():
    """⛔ A MEASUREMENT TAKEN OUTSIDE THE GUARD IS A MEASUREMENT OF ANOTHER PATH (round 20).

    `run()` pops `TORTOISE_UNDERSCORE_TERMS` / `TORTOISE_FUSED_RERANK` for every arm and
    restores the ambient values in a `finally`. The mint arm — published as the
    **shipped matcher**, and the source of §3b/§4's `1.0000 -> 0.5328` — used to be
    measured AFTER that `finally`, and recorded no `env` field at all. On a box
    exporting `TORTOISE_UNDERSCORE_TERMS=0` (the documented bisect switch, and exactly
    how the `*_pre_lever` arms are produced) the arm then measured the PRE-lever
    retrieval path while the record called it shipped, undeclared.

    Asserted on the AST rather than on formatted text, so a reflow of `run()` cannot
    false-red this, and MOVING THE CALL OUT OF THE `try` (the defect) does red it.
    """
    fn = next(n for n in ast.walk(ast.parse(inspect.getsource(cap.run)))
              if isinstance(n, ast.FunctionDef))
    calls = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "mint_rate"]
    assert calls, "`run()` no longer calls `mint_rate` — the mint arm is the shipped matcher"
    guards = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and any(
        isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
        and c.func.attr == "pop" and isinstance(c.func.value, ast.Attribute)
        and c.func.value.attr == "environ"
        for stmt in n.finalbody for c in ast.walk(stmt))]
    assert len(guards) == 1, (
        f"expected exactly one env-restoring (`os.environ.pop` in its `finally`) "
        f"try/finally in `run()`, found {len(guards)}")
    inside = range(guards[0].body[0].lineno, guards[0].body[-1].end_lineno + 1)
    assert calls[0] in inside, (
        "`mint_rate` is measured OUTSIDE the env guard, so an ambient "
        "`TORTOISE_UNDERSCORE_TERMS=0` turns the arm published as the SHIPPED matcher "
        "into a second pre-lever arm without the record saying so — measure it inside "
        "the `try`, with the shipped state set explicitly")
    # ⛔ SCOPED TO THE MINT ARM'S OWN STATEMENT (round 21, measured). The first cut built
    # `env_keys` from `ast.walk(fn)`, so the ARMS loop's `arms[arm]["env"] = dict(env)`
    # satisfied it on its own: deleting the mint arm's `env` assignment stayed GREEN.
    # A key set over the whole function cannot say anything about one statement in it.
    mint_env = [n for n in ast.walk(fn)
                if isinstance(n, ast.Subscript) and isinstance(n.ctx, ast.Store)
                and isinstance(n.slice, ast.Constant) and n.slice.value == "env"
                and isinstance(n.value, ast.Subscript)
                and isinstance(n.value.slice, ast.Constant)
                and n.value.slice.value == "mint_rate"]
    assert mint_env, (
        "the mint arm must record the `env` it ran under, like every other arm, and "
        "not only through the ARMS loop's write: without it a reader cannot tell which "
        "retrieval path §3b came from")
    # ⛔ AND THE SHIPPED STATE IS SET EXPLICITLY, NOT INHERITED FROM THE LOOP (rounds 21-22).
    # "inside the `try`" is not the fix on its own: the loop's LAST arm leaves its own
    # `env` overrides in place (`user_search_rerank` sets `TORTOISE_FUSED_RERANK=1`), so a
    # mint arm measured straight after it carries them while recording `env: {}`.
    #
    # ⛔ THIS BINDS THE LOOP BY IDENTITY, NOT BY POSITION (round 22, measured). The first
    # cut took `[n for n in guards[0].body if isinstance(n, ast.For)][0]` — the FIRST `for`
    # in the `try` body, which need not be the ARMS loop at all. Moving the explicit pop
    # block ABOVE `for arm, door, env in ARMS` (the defect) then made that loop the
    # first-for, whose own body supplies a later pop, and the pin stayed GREEN on the very
    # failure its message forbids; so did deleting the block and inserting any unrelated
    # `for xx in (): pass` before the ARMS loop.
    # ⛔ AND THE POP MUST COVER **BOTH** VARIABLES (round 22, measured). The first cut
    # recorded line numbers only, so replacing the two-key `for key in saved: pop(key)`
    # with a single `pop("TORTOISE_UNDERSCORE_TERMS")` left `TORTOISE_FUSED_RERANK=1`
    # standing into `mint_rate(...)` — the shipped matcher measured rerank-armed — with
    # the pin GREEN. It now requires a loop over `saved`, which is the only shape that
    # covers every key the guard saved, and asserts `saved` is built from both names.
    env_names = {"TORTOISE_UNDERSCORE_TERMS", "TORTOISE_FUSED_RERANK"}
    present = {n.value for n in ast.walk(fn)
               if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert env_names <= present, (
        f"`run()` no longer names both env switches it must neutralise for the shipped "
        f"arm — missing {sorted(env_names - present)}")
    arm_loops = [n for n in guards[0].body
                 if isinstance(n, ast.For) and isinstance(n.iter, ast.Name)
                 and n.iter.id == "ARMS"]
    assert len(arm_loops) == 1, (
        f"expected exactly one `for ... in ARMS` loop in the env guard's `try` body, "
        f"found {len(arm_loops)} — the mint-arm assertions below are scoped to it")
    loop = arm_loops[0]
    pops_all = [n for stmt in guards[0].body for n in ast.walk(stmt)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "pop" and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "environ"]
    assert any(n.lineno > loop.end_lineno and n.lineno < calls[0] for n in pops_all), (
        "`run()` must pop the ambient switches AFTER the ARMS loop and BEFORE measuring "
        "the mint arm (the SHIPPED matcher), rather than inheriting whatever the loop's "
        "last iteration left set — the loop's own per-arm pop does not satisfy this")
    shipping_resets = [s for s in guards[0].body
                       if isinstance(s, ast.For) and isinstance(s.iter, ast.Name)
                       and s.iter.id == "saved"
                       and s.lineno > loop.end_lineno and s.end_lineno < calls[0]
                       and any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                               and n.func.attr == "pop" for n in ast.walk(s))]
    assert len(shipping_resets) == 1, (
        f"expected exactly one `for key in saved: os.environ.pop(key, None)` between the "
        f"ARMS loop and the `mint_rate` call, found {len(shipping_resets)} — that loop is "
        f"the only shape that neutralises EVERY saved switch, so a single-variable pop "
        f"leaves the last arm's `TORTOISE_FUSED_RERANK=1` standing into the mint arm")


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

    from tortoise import mcp_server, selfhost_api

    sig = inspect.signature(mcp_server.tortoise_search)
    door_entity = sig.parameters["entity_type"].default
    door_limit = sig.parameters["limit"].default

    # ⛔ BOTH doors, not one. The note says "both doors default to 'point' / 10", and
    # reading only the MCP signature enforced half of that: raising the REST route's
    # `Query(10)` to `Query(50)` would make the note false about the REST door with the
    # suite green — and `limit=10` on REST is load-bearing, because it is what makes
    # §5's `k <= 10` bound TRUE. FastAPI wraps the default in `Query`, hence `.default`.
    rest_sig = inspect.signature(selfhost_api.search)
    assert "entity_type" not in rest_sig.parameters, (
        "the REST door now takes its own `entity_type`, so it no longer inherits the "
        "SDK default the note attributes to it — DOOR_NOTES must be re-stated")
    rest_limit = rest_sig.parameters["limit"].default
    rest_limit = getattr(rest_limit, "default", rest_limit)
    assert rest_limit == door_limit, (
        f"the two doors disagree on `limit` (MCP {door_limit!r}, REST "
        f"{rest_limit!r}) while DOOR_NOTES says they share one default")
    # …and, for completeness, that the REST route really resolves to the same verb.
    rest_body = inspect.getsource(selfhost_api.search)
    assert "tortoise_fts_query" in rest_body, (
        "the REST `/v1/search` route no longer calls `sdk.tortoise_fts_query`, so it is "
        "no longer the door this record measured through")

    # ⛔ AND resolve what the REST route INHERITS. `"entity_type" not in
    # rest_sig.parameters` only proves the route declares no override — it says nothing
    # about the default it gets from the SDK, which is the value the note attributes to
    # the REST door. Measured: changing `TortoiseSDK.tortoise_fts_query`'s
    # `entity_type: str = "point"` -> `"object"` made the REST door default to
    # `'object'` while the note and §5 both said `'point'`, and the suite stayed GREEN.
    from tortoise.sdk import TortoiseSDK

    sdk_entity = inspect.signature(
        TortoiseSDK.tortoise_fts_query).parameters["entity_type"].default
    assert sdk_entity == door_entity, (
        f"the REST door inherits `entity_type` from the SDK ({sdk_entity!r}), which no "
        f"longer matches the MCP door ({door_entity!r}) — the note's \"both doors "
        f"default to {door_entity!r}\" is now false for REST")

    # ⛔ AND the instrument's OWN docstring must not restate the falsehood the note
    # dropped. Measured: the module docstring still said `user_search` runs "at that
    # tool's own defaults" forty lines above the note that calls that phrasing false —
    # so a reader who opened the tool got the pre-fix claim.
    module_doc = cap.__doc__ or ""
    assert "own defaults" not in module_doc, (
        "the tool's module docstring still says the arms run at the tool's \"own "
        "defaults\" — the exact claim `DOOR_NOTES` and README §5 declare false")
    assert "entity_type='object'" in module_doc, (
        "the module docstring must state the arguments the arms actually pass, in the "
        "same verb/arguments terms as `DOOR_NOTES`")

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
    assert "entity_type='object'" in note, (
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

    # ⛔ …and the README's OWN restatement of the same boundary, which was left
    # unpinned by that fix and is the same defect one document over. §5 opens with a
    # *frontier* claim of its own ("the measured call is the user door's VERB, not its
    # ARGUMENTS") and prints both diverging defaults as checked facts; `limit=10` →
    # `limit=20` in that paragraph stayed GREEN with every pin above unchanged
    # (measured at this head). A corrected constant beside an unpinned restatement is
    # exactly the claim/evidence split this file exists to close, so the paragraph is
    # pinned clause-scoped, in the same DERIVED terms rather than hard-coded digits.
    readme = README.read_text()
    for marker in ("**First, the boundary on the door itself",
                   "**Not a production census.**"):
        assert marker in readme, (
            f"README §5 must carry {marker!r} — it is the bound this paragraph is "
            f"pinned against; if the section was reworded, re-point this check")
    door_para = " ".join(readme[
        readme.index("**First, the boundary on the door itself"):
        readme.index("**Not a production census.**")].split())
    assert "entity_type='object'" in door_para, (
        f"README §5 must state the `entity_type` the arms pass: {door_para!r}")
    assert _phrase_mentions(door_para, f"limit={cap.KMAX}"), (
        f"README §5 must state the `limit` the arms pass ({cap.KMAX}): {door_para!r}")
    assert f"default to `entity_type='{door_entity}'`" in door_para, (
        f"README §5 must name the doors' real `entity_type` default "
        f"({door_entity!r}) so the divergence is visible: {door_para!r}")
    assert _phrase_mentions(door_para, f"limit={door_limit}"), (
        f"README §5 must name the doors' real `limit` default ({door_limit!r}) — "
        f"the shipped window is that value, and a wrong one here re-publishes the "
        f"falsehood as a checked fact: {door_para!r}")
    assert f"shipped-window figure only for `k <= {door_limit}`" in door_para, (
        f"README §5 must scope `recall@k` to the doors' own window "
        f"(`k <= {door_limit}`), not to this instrument's `@{cap.KMAX}`: "
        f"{door_para!r}")
    assert "own defaults" not in door_para, (
        "README §5 calls this fixture's arguments the tool's \"own defaults\" "
        f"while diverging from {door_entity!r}/{door_limit!r}: {door_para!r}")


def test_the_arms_pass_the_arguments_the_door_notes_say_they_do():
    """The note's ARGUMENT half, driven — not compared to itself.

    ⛔ MEASURED DEFECT this pins, and it survived the fix above. That test reads the
    doors' signatures and then asserts the note names `entity_type='object'` /
    `limit=KMAX`; **nothing read what the arms actually pass**. Both halves red as
    survivors at `eb00ee8c` → this file's head:

        mutant A  tools/capstone_12186_recall.py:508
                  _query_rows(door, ctx, row["name"], KMAX) -> (..., 10)
                  (KMAX stays 15, so every f-string in the note still agrees)
                  result: 0 RED — the record keeps publishing "the arms call
                  limit=15" after the arms stop calling it.
        mutant B  tools/capstone_12186_recall.py:451
                  entity_type="object" -> "point" in `_query_rows`
                  result: 0 RED — same defect, the other argument.

    This is the exact shape that test rejects on the DOOR side (`_user_door_binding`
    compared a derived sentence to itself). Here the sentence is compared to the
    CALL: `measure_arm` is driven for every arm `ARMS` declares, with a recording
    sdk, and the arguments it receives are compared against the literals the
    door's own note publishes — extracted FROM the note, so a note reworded to a
    different argument reds instead of quietly agreeing with the code again.

    Hermetic like the rest of this file: no graph, no measurement, ~50 ms.
    """
    def _record(arms_seen: list, door_calls: list):
        class _Sdk:
            def tortoise_fts_query(self, query, **kwargs):
                door_calls.append({"query": query, **kwargs})
                return []

        def _fts_rows(sdk, cls, query, **kwargs):
            # `capture_prior` is handed the entity type POSITIONALLY (`_query_rows`
            # calls `ctx["fts_rows"](wrapped, "object", query, limit=...)`), so it is
            # recorded under the same key the SDK kwarg uses — the note makes one
            # claim about the arms, whichever door carries it.
            door_calls.append({"entity_type": cls, "query": query, **kwargs})
            return []

        return {"sdk": _Sdk(), "fts_rows": _fts_rows, "modes": {},
                "legs": {}, "leg_reasons": {}, "rerank_ran": 0}

    # The note is the claim; the call is what must satisfy it. Read the entity_type
    # and (where stated) the limit out of each door's own prose.
    stated = {}
    for door in cap.DOORS:
        note = cap.DOOR_NOTES[door]
        ent = re.search(r"entity_type='([a-z_]+)'", note)
        assert ent, (
            f"DOOR_NOTES[{door!r}] names no `entity_type='...'` for the arms to "
            f"pass, so this pin has nothing to hold it to: {note!r}")
        lim = re.search(r"\blimit=(\d+)", note)
        stated[door] = (ent.group(1), int(lim.group(1)) if lim else None)

    # `user_search`'s note publishes the argument the whole shipped-window caveat
    # rests on, so it must state one; `capture_prior`'s does not claim a `limit`.
    assert stated["user_search"][1] == cap.KMAX, (
        f"DOOR_NOTES['user_search'] publishes `limit={stated['user_search'][1]}` but "
        f"the arms run at KMAX={cap.KMAX}: the caveat `recall@k is a shipped-window "
        f"figure only for k <= the doors' default` is stated against a window this "
        f"instrument does not measure")

    row = {"id": "p1", "name": "the merge-base", "cls": "core"}
    for arm, door, _env in cap.ARMS:
        calls: list = []
        ctx = _record([], calls)
        cap.measure_arm(arm, door, [row], ctx)
        assert len(calls) == 1, (
            f"arm {arm!r} (door {door!r}) made {len(calls)} read(s) for one query; "
            f"this pin drives one and compares the arguments: {calls!r}")
        call = calls[0]
        want_entity, want_limit = stated[door]
        assert call["query"] == row["name"], (arm, call)
        assert call.get("entity_type") == want_entity, (
            f"arm {arm!r} passes entity_type={call.get('entity_type')!r} while its "
            f"door note says the arms pass {want_entity!r} — the note describes a "
            f"call the instrument does not make")
        if want_limit is not None:
            assert call.get("limit") == want_limit, (
                f"arm {arm!r} passes limit={call.get('limit')!r} while its door note "
                f"says the arms pass limit={want_limit} — this is the survivor that "
                f"kept `limit=15` published after the arms moved to another window")


def test_the_record_says_code_head_is_a_label_and_names_a_runnable_anchor(artifact):
    """`code_head` survives a rebase as a LABEL, so the record must not run it.

    ⛔ MEASURED DEFECT this pins. `code_head` is the run head `31bce52ddb`, which
    predates the rebase onto `origin/main`; the rebase REWROTE that sha, so it is an
    ancestor of no ref on the published branch:

        $ git merge-base --is-ancestor 31bce52ddb HEAD   -> exit 1
        $ git branch -a --contains 31bce52ddb           -> (empty)
        $ git show 31bce52ddb:tools/capstone_12186_recall.py
        fatal: invalid object name '31bce52ddb'.

    README §6 presented that `git show` as the evidence for the `cleanup` hand-edit,
    so the record's reproduction anchor resolved only on the machine that held the
    pre-rebase object.

    ⛔ AND A FIRST ATTEMPT AT THIS PIN WAS WORSE THAN THE DEFECT (round 3, P1). It
    hard-coded `318950c6e0` and asserted that sha resolved, was an ancestor, and had
    blob-identical inputs — then `pytest.skip`ped when the repo was shallow. That made
    it RED on any fresh clone (partial history, not flagged shallow) and INERT in CI
    (`python-ci.yml`'s test job checks out at the default depth 1, so every one of
    those assertions was skipped). A gate that runs only where it cannot answer is a
    no-op gate — the ruling this repo already recorded for #11057's provenance arm.

    So the shape here matches that ruling: **fail closed, and assert the thing that is
    true in EVERY checkout.** What is true everywhere is that the record must tell a
    reader to run against `HEAD` (always present) rather than against the label, and
    must say how to obtain history if the clone is shallow. Existence of the OLD label
    is deliberately NOT asserted — it is unknowable in a shallow clone by
    construction, and the record no longer asks anyone to resolve it.
    """
    note = artifact["code_head_note"]
    assert note == cap._code_head_note(artifact["code_head"]), (
        "`code_head_note` must be what `_code_head_note` BUILDS from `code_head`: a "
        "hand-typed reconciliation string is the free-text note that "
        "`post_run_edits` exists to replace")

    label = artifact["code_head"][:12]
    assert label in note, "the note must name the run head it is explaining"
    # The point of the note: `code_head` is a LABEL, and reproduction runs at HEAD.
    assert "LABEL" in note, (
        "the note must say `code_head` is a label, not a runnable anchor")
    assert "HEAD" in note, (
        "the note must name the ref a reader should actually run commands against")
    assert "--unshallow" in note, (
        "the note must tell a shallow-clone reader how to obtain history "
        "(`git fetch --unshallow`) — otherwise the instruction silently fails for them")
    # ⛔ THE REBASE IS STATED AS A RULE, NOT AS A COMPLETED EVENT (round-14 P2,
    # measured). The note is written AT the run head, where a rebase has not happened
    # yet — the head IS the branch tip and is an ancestor of itself — so a note that
    # says the rebase "REWROTE" the sha records an event its own writer cannot observe.
    # (For THIS record the rebase has since happened, which is exactly why the wording
    # must not depend on that: the builder is what a FUTURE run emits, and it emits the
    # note before any rebase.) Assert the RULE form, and assert the past-tense claim is
    # gone (mutation: restore it -> red).
    assert "a rebase rewrites" in note, (
        "`code_head_note` must state the RULE (`a rebase rewrites <label>`), which is "
        "true at write time and after — not an `if` that implies it has not happened, "
        "and not a past tense that implies the writer watched it happen")
    assert "REWROTE" not in note and "predates this branch's rebase" not in note, (
        "`code_head_note` asserted the rebase in the past tense, as though it had "
        "already rewritten the run head — at write time the run head is the branch tip")

    readme = README.read_text()
    flat = " ".join(readme.split())
    # §6 must hand out the runnable form, never the label, and must carry the remedy.
    assert "--unshallow" in flat, (
        "README §6 must carry the shallow-clone remedy, matching `code_head_note`")
    assert "git show HEAD:tools/capstone_12186_recall.py" in flat, (
        "README §6's reproduction command must be written against `HEAD`, which "
        "resolves in every checkout — a command written against the pre-rebase label "
        "fails with `fatal: invalid object name`")
    # ⛔ The regression itself: §6 must not present the label as the thing to run.
    #
    # ⛔ THIS GUARD WAS KEYED TO ONE SPELLING AND WAS THEREFORE INERT (found by a
    # fresh-context review, verified by execution). It used to assert the ABSENCE of
    # `git show {code_head[:12]}:` — that is `git show 31bce52ddbb3:`. The README's own
    # counter-example abbreviates to TEN characters (`31bce52ddb`), and a `git show
    # <sha>` abbreviation is a prefix of ANY length >= 7, so rewriting that line into an
    # INSTRUCTION in the ten-character spelling left the suite GREEN at 47 passed. A
    # guard that matches a literal token can only ever see the one token it names, so
    # this now derives the forbidden set from `code_head` at EVERY abbreviation length
    # a reader could type, and refuses any `git show <hex>:` that is a prefix of it.
    shown = set(re.findall(r"git show ([0-9a-f]{7,40}):", flat))
    code_head = artifact["code_head"]
    for token in sorted(shown):
        # A sha abbreviation is a prefix; comparing with a common prefix of any length
        # >= 7 closes the spelling hole the twelve-character form left open.
        if len(token) >= 7 and code_head.startswith(token):
            raise AssertionError(
                f"README §6 still hands out an unresolvable command (`git show {token}:`), "
                f"which is a {len(token)}-character abbreviation of `code_head` — that is "
                f"exactly the defect this record was corrected for. Write reproduction "
                f"commands against `HEAD`, or against the reachable equivalent the "
                f"masthead names.")
    # The one sha-shaped hand-out the record is ALLOWED is the reachable equivalent in
    # the masthead; anything else is an unpinned integer-in-prose. Pin the hand-out
    # itself so it cannot drift to a foreign sha unnoticed (a `git show <hex>:` whose
    # token is neither `code_head`-prefixed nor the declared equivalent is a NEW
    # instruction nobody reviewed).
    named = re.search(r"reachable equivalent is `([0-9a-f]{7,40})`", flat)
    assert named, (
        "the masthead must name the reachable equivalent a reader can actually run — "
        "`code_head` itself is a pre-rebase label and resolves on no published ref")
    equivalent = named.group(1)
    assert not code_head.startswith(equivalent), (
        f"the masthead names {equivalent!r} as a reachable equivalent, but it is a "
        f"prefix of `code_head` {code_head!r} — that is the same unresolvable sha "
        f"under a different abbreviation, not an equivalent")
    assert shown <= {equivalent}, (
        f"README hands out `git show <sha>:` commands against {sorted(shown)}, but the "
        f"only sha the record declares reachable is {equivalent!r}; every other sha in "
        f"that form is an unpinned hand-out")

    # ⛔ THE CLAUSE THAT MOTIVATED THIS PIN, CHECKED AGAINST ITSELF. Verified defect at
    # this head: §6's `cleanup` item read "the tool at `code_head` has no `cleanup`
    # builder at all (git show HEAD:... | grep -c 'def _cleanup'; ...)" — and executing
    # that citation prints 1, i.e. the cited command REFUTED the sentence it was cited
    # for. A citation whose own output contradicts its clause is worse than no citation,
    # because a reader who runs it concludes the record is wrong (or that the clause is).
    # The clause must therefore cite the head that HAS no builder and state that count.
    clause_at = flat.index("the block was added by hand")
    clause = flat[clause_at:flat.index("`arms`** — the three `user_search*")]
    assert f"git show {equivalent}:tools/capstone_12186_recall.py" in clause, (
        f"§6's `cleanup` clause must cite `git show {equivalent}:` — the run-head "
        f"equivalent is the head with NO builder. The clause as committed cites "
        f"{sorted(shown)}; a `HEAD`-anchored command prints 1 and refutes the clause")
    assert "**`0`**" in clause, (
        "§6's `cleanup` clause must state the count its citation produces (`0`), so a "
        "reader does not have to run it to learn that the run head emits no block")
    # …and the artifact's own declaration must name the SAME head, or the two documents
    # disagree about which sha proves the claim (the exact divergence this round found).
    declared_why = next(e["why"] for e in artifact["post_run_edits"]
                        if e["field"] == "cleanup")
    assert f"git show {equivalent}:" in declared_why, (
        f"`post_run_edits`' cleanup reason cites a different head than README §6 — it "
        f"must name `git show {equivalent}:` too, so the record and its summary cannot "
        f"state the `cleanup` provenance against two different shas. It reads: "
        f"{declared_why[:200]!r}")

    # ⛔ AND THE EQUIVALENT IS BRANCH-LOCAL TOO, WHICH THE RECORD MUST SAY (F3, verified):
    # `git merge-base --is-ancestor 318950c6e0 origin/main` -> exit 1, i.e. the sha the
    # masthead calls "reachable" is one of THIS branch's commits. A rebase rewrites it
    # exactly as it rewrote `code_head`, so the equivalent is a convenience for a reader
    # who already holds the pre-rebase object, never a durable anchor. Pinning the
    # hand-out while calling it "reachable" without this sentence is how a record
    # certifies a label it cannot make resolve.
    assert "branch-local" in clause, (
        "§6's `cleanup` clause must say the equivalent sha is BRANCH-LOCAL (a rebase "
        "strands it like the run head), so no reader treats it as a durable anchor")

    # The note is a DECLARED edit, not free text the ledger cannot see (round-3 P1).
    declared = {e["field"] for e in artifact["post_run_edits"]}
    assert "code_head_note" in declared, (
        f"`code_head_note` is a hand-written field, so it must be declared in "
        f"`post_run_edits`; declared: {sorted(declared)}")


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


def test_the_uri_guard_accepts_every_scheme_the_product_itself_supports():
    """The accepted set is the PRODUCT's, and `rediss://` is in it (round-22 F1).

    The pre-fix guard was `uri.startswith(("docker://", "redis://"))`, inherited
    verbatim from `tools/candidate_recall_11901.py:1400`, so a `rediss://` URI —
    FalkorDB Cloud, the scheme `tortoise/config.py:50 SUPPORTED_URI_SCHEMES`
    declares and `projection._validate_uri_scheme` reuses — was refused with the
    sentence *"a backend the product cannot use"*, while the
    `FalkorProjection.from_uri` three lines below would have connected to it.

    ⛔ BOTH HALVES ARE ASSERTED. The equality pins DRIFT (a scheme added to, or
    dropped from, the product's own tuple reddens here rather than silently
    diverging); the loop pins the BEHAVIOUR, so the mutant that matters — deleting
    `"rediss://"` from `URI_PREFIXES` — fails with the scheme named instead of
    only on a tuple mismatch.
    """
    from tortoise.config import SUPPORTED_URI_SCHEMES

    assert tuple(f"{s}://" for s in SUPPORTED_URI_SCHEMES) == cap.URI_PREFIXES, (
        f"the guard accepts {cap.URI_PREFIXES!r}; the product declares "
        f"{SUPPORTED_URI_SCHEMES!r} (tortoise/config.py). A scheme the product "
        f"connects to but this instrument refuses is a refusal that states "
        f"something false about the product")
    for scheme in SUPPORTED_URI_SCHEMES:
        assert cap._uri_refusal(f"{scheme}://:pw@host:6379/g") is None, (
            f"`{scheme}://` is in SUPPORTED_URI_SCHEMES, so refusing it reports a "
            f"backend the product cannot use that the product does use")

    # The inverse direction: an unrelated scheme MUST still be refused, or the
    # acceptance above is satisfied by a predicate that accepts everything.
    assert cap._uri_refusal("postgres://host/db") is not None
    assert cap._uri_refusal("") is not None, (
        "an unset TORTOISE_DB_URI must refuse: a recall figure measured on no "
        "backend at all is not a figure")


def test_run_does_not_refuse_a_rediss_uri_the_product_uses(monkeypatch):
    """`run()` must not stop at the URI arm for a backend the product connects to.

    Drives the REAL `run()` with the door guard satisfied and a `rediss://` URI,
    and stubs the projection so the only way to reach it is to have PASSED the URI
    guard. Exit-code-free on purpose: `run()` has four `return 3` arms, so a test
    that only asserted `rc != 3` would be satisfied by any other refusal — the
    assertion here is that execution reached `FalkorProjection.from_uri` at all.

    Mutant: drop `"rediss://"` from `URI_PREFIXES` → the URI arm answers and this
    test fails on the un-raised sentinel, not on a message.
    """
    import argparse

    class _PastTheUriGuard(Exception):
        pass

    class _Boom:
        @staticmethod
        def from_uri(*_a, **_k):
            raise _PastTheUriGuard("reached FalkorProjection.from_uri")

    import tortoise.projection

    monkeypatch.setattr(tortoise.projection, "FalkorProjection", _Boom)
    monkeypatch.setenv("TORTOISE_DB_URI", "rediss://:pw@falkordb.example:6379/g")
    monkeypatch.setattr(cap, "_user_door_binding", lambda: {
        "tool": "tortoise_search", "present": True,
        "matches_measured_call": True, "sdk_method": "tortoise_fts_query",
        "rest": "GET /v1/search"})
    args = argparse.Namespace(pin=str(PIN), graph="cap-test", namespace=None,
                              out=None)
    with pytest.raises(_PastTheUriGuard):
        cap.run(args)


def test_run_refuses_a_uri_the_product_cannot_use(monkeypatch, capsys):
    """The inverse: a scheme the product cannot use still refuses, with its reason.

    Without this row, `_uri_refusal` returning `None` unconditionally would leave
    the acceptance pins green while the guard became decorative — the exact shape
    this file's rule 3 names.
    """
    import argparse

    monkeypatch.setenv("TORTOISE_DB_URI", "postgres://host/db")
    monkeypatch.setattr(cap, "_user_door_binding", lambda: {
        "tool": "tortoise_search", "present": True,
        "matches_measured_call": True, "sdk_method": "tortoise_fts_query",
        "rest": "GET /v1/search"})
    args = argparse.Namespace(pin=str(PIN), graph="cap-test", namespace=None,
                              out=None)
    assert cap.run(args) == 3
    err = capsys.readouterr().err
    assert "no live FalkorDB" in err, (
        f"the URL guard did not refuse an unsupported scheme: {err!r}")


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

    ⛔ `31bce52ddb` is the RUN head, and the rebase onto `origin/main` rewrote it, so
    the `git show` above resolves on no ref of this branch. Run the identical command
    against `318950c6e0` (this branch, byte-identical tool + `queries.json` blobs) —
    the mapping is `artifact["code_head_note"]` and README §6 states it too.

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
        "318950c6e0:tools/capstone_12186_recall.py | grep -c cleanup` = 0 — use "
        "318950c6e0, not the rebased-away run head; see `code_head_note`). If you "
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
    # ⛔ BOTH serialisations. `paired[...]` and `merged_lever_visibility[...]` are the
    # SAME dict in the tool, but the committed record's merged copies were written by a
    # tool that predates the truncation declaration — so the defect this pin exists for
    # (`gains: 41` beside 20 ids, no marker) survived in the block §4 and the abstract
    # cite, while the `paired` copy was repaired. Iterate every paired-shaped block,
    # wherever it lives.
    cells = [("paired", name, blk["paired"])
             for name, blk in artifact["paired"].items()]
    cells += [("merged_lever_visibility", name, blk)
              for name, blk in artifact["merged_lever_visibility"].items()
              if isinstance(blk, dict) and "gain_ids" in blk]
    assert len(cells) >= 6, f"expected both serialisations of every block: {len(cells)}"
    for where, name, cell in cells:
        assert cell["queries_compared"] + cell[
            "excluded_degraded_on_either_side"] == artifact["pin"]["queries"], (where, name)
        assert cell["gains"] + cell["losses"] <= cell["queries_compared"], (where, name)

        # ⛔ A capped id list beside an uncapped COUNT is a silent subset. Measured:
        # `capture_prior_pre_lever -> capture_prior` publishes `gains: 43` beside 20
        # ids and said nothing, so a reader re-deriving the count from the ids got 20
        # — the `tortoise/truncation.py::mark_truncation` class. `gains`/`losses`
        # carry the totals; these assert the cap is PUBLISHED and that the published
        # flag actually matches whether it bit.
        assert cell["ids_truncated_at"] == cap.IDS_LIMIT, (
            f"{where}[{name}]: `ids_truncated_at` must publish the cap "
            f"({cap.IDS_LIMIT})")
        assert len(cell["gain_ids"]) == min(cell["gains"], cap.IDS_LIMIT), (
            f"{where}[{name}]: `gain_ids` has {len(cell['gain_ids'])} entries for "
            f"{cell['gains']} gains — the list is neither full nor capped at "
            f"{cap.IDS_LIMIT}")
        assert len(cell["loss_ids"]) == min(cell["losses"], cap.IDS_LIMIT), (
            f"{where}[{name}]: `loss_ids` has {len(cell['loss_ids'])} entries for "
            f"{cell['losses']} losses")
        assert cell["gain_ids_truncated"] == (cell["gains"] > cap.IDS_LIMIT), (where, name)
        assert cell["loss_ids_truncated"] == (cell["losses"] > cap.IDS_LIMIT), (where, name)
        # …and every listed id is a real query, so a padded list cannot satisfy the
        # length check above.
        known = set(artifact["arms"]["user_search"]["per_query"])
        assert set(cell["gain_ids"]) <= known, (where, name, "unknown gain id")
        assert set(cell["loss_ids"]) <= known, (where, name, "unknown loss id")
        assert len(set(cell["gain_ids"])) == len(cell["gain_ids"]), (where, name, "dup gain id")
        assert len(set(cell["loss_ids"])) == len(cell["loss_ids"]), (where, name, "dup loss id")


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
    # The prose is LINE-WRAPPED, so a literal `in` misses a sentence that spans two
    # source lines. Normalise runs of whitespace to one space before matching — the
    # figures are what is being pinned, not the wrapping.
    flat = " ".join(text.split())
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
    assert _phrase_mentions(flat, own), (
        f"README §2a must quote the rerank arm's own degradation pair {own!r} "
        f"(artifact holds {r_kw} keyword-only / {r_zero} empty, NOT the shipped "
        f"arm's {s_kw}/{s_zero}) — this is the exact figure that regressed once")
    # …and it must say which arm's pair it is NOT, so a reader cannot reattribute it.
    not_shipped = f"not the shipped arm's {s_kw}/{s_zero}"
    assert _phrase_mentions(flat, not_shipped), (
        f"README §2a must name the shipped arm's pair ({not_shipped!r}) as the one "
        f"this sentence is NOT quoting; otherwise the two pairs are indistinguishable")

    # §5 — the mint rate restated as an upper bound, derived from the record.
    rate = f"{artifact['arms']['mint_rate']['shipped']['rate'] * 100:.1f} %"
    assert _phrase_mentions(flat, f"The {rate} mint rate is therefore an"), (
        f"README §5 must restate the mint rate as {rate!r} (the artifact's "
        f"`arms.mint_rate.shipped.rate`), so the declared upper bound cannot "
        f"contradict §3b's table with a green suite")

    # §3a's headline sentence — the k=3 cell, and the k=10 cell it is NOT.
    # ⛔ MEASURED: the sentence read "~2.3 % of what a user is handed at the
    # production-style width". 2.3 % IS the k=3 cell (0.0229), but 3 is the CAPTURE
    # PRIOR's production window (`cap.KS` docstring) — the user door's shipped width
    # is 10, where the rate is 1.4 %. So the figure was right and its label was
    # wrong; §5's own new sentence is what made the clash visible.
    us = artifact["arms"]["user_search"]["duplicate_rate_in_window"]
    k3 = f"~{us['3']['rate'] * 100:.1f} %"
    k10 = f"{us['10']['rate'] * 100:.1f} %"
    assert _phrase_mentions(flat, f"{k3} of the top 3"), (
        f"README §3a must state the duplicate rate as {k3!r} **at k=3** — the k=3 "
        f"cell, labelled with the window it belongs to")
    assert _phrase_mentions(flat, f"the rate is **{k10}**"), (
        f"README §3a must also quote the user door's own shipped width (k=10, "
        f"{k10!r}) so the two windows cannot be confused")


def test_section_fives_numbered_limits_five_to_seven_state_the_figures_the_artifact_holds(
        artifact):
    """§5 items 5–7 were DECLARED covered by exclusion, and asserted nowhere.

    ⛔ MEASURED DEFECT this pins (the round-14 handover's F4). The pinned/not-pinned
    boundary in this module's docstring excluded "§5's declared LIMITS (items 1-4)",
    which tells a reader that items 5–7 ARE enforced. Nothing enforced them: mutating
    them stayed green, so the document could publish an oversampling share, a
    per-query quantum and an exclusion count that the record contradicts, under a
    declaration saying it could not.

    ⚠️ F4 AS HANDED OVER WAS ITSELF PARTLY WRONG, and a false correction here would
    have made the record WORSE. It proposed `+7.13 pts, 9 gained / 0 lost` for §2a and
    `≈5 %` / `±0.55 pts` for §5. The artifact holds `delta_pts_clean: 1.13`,
    `gains: 3`, `losses: 0` for `user_search -> user_search_rerank` (so §2a's
    `+1.13 pts, 3 gained / 0 lost` is CORRECT), and with `n = 300` one query is
    1/300 = 0.33 pts, not 0.55; the strings `7.13`, `9 gained` and `0.55` occur
    nowhere in `measurement.json`. Only F4's SECOND half — the boundary claiming
    coverage it did not have — was real, and this test is what closes it.

    Every figure is DERIVED from the record, never hard-coded, so the prose cannot
    drift from the artifact.
    """
    plain = " ".join(README.read_text().split())

    # §5.5 — the oversampling shares, from the pin's own class counts.
    art_pin = artifact["pin"]
    ident_pct = art_pin["shares"]["ident"] / art_pin["queries"] * 100
    path_pct = art_pin["shares"]["path"] / art_pin["queries"] * 100
    shares = f"{ident_pct:.0f} % / {path_pct:.0f} %"
    assert _phrase_mentions(plain, f"({shares} against"), (
        f"README §5.5 must state the oversampling as `{shares}` — derived from the "
        f"artifact pin's `shares` ({art_pin['shares']}) over `queries` ({art_pin['queries']})")
    # …and the EXTERNAL production share it is compared against is named as such: no
    # artifact holds it, which is why it stays in the unpinned set below rather than
    # being silently counted as covered.
    assert "measured production share of" in plain, (
        "README §5.5 must keep naming the ≈2 % production share as a MEASURED "
        "external figure — it is the one clause of §5.5 the record cannot hold")

    # §5.6 — the query count and the per-query quantum it implies.
    n = artifact["arms"]["user_search"]["n"]
    quantum = 100 / n
    assert _phrase_mentions(plain, f"n = {n} queries"), (
        f"README §5.6 must state the run's window count as `n = {n} queries` "
        f"(artifact `arms.user_search.n`)")
    assert _phrase_mentions(plain, f"±{quantum:.2f} pts"), (
        f"README §5.6 must state one query's weight as ±{quantum:.2f} pts with "
        f"n = {n} — a different quantum beside the same n is a contradiction")

    # §5.7 — the exclusion count, and the denominator it is out of.
    excluded = artifact["paired"]["user_search_pre_lever -> user_search"]["paired"][
        "excluded_degraded_on_either_side"]
    assert _phrase_mentions(plain, f"{excluded} of {n} user-door queries were excluded"), (
        f"README §5.7 must state `{excluded} of {n} user-door queries were excluded` "
        f"(artifact paired['user_search_pre_lever -> user_search']."
        f"excluded_degraded_on_either_side = {excluded})")

    # ⛔ THE DECLARATION must not read as covering more than this file enforces. With
    # 5–7 pinned above, the ONLY part of §5 still outside the claim is item 5's
    # external production share; the docstring must say exactly that, so the next reader
    # cannot conclude that an unpinned §5 clause (or a pinned one) is the reverse.
    doc = __doc__ or ""
    assert "item 5's EXTERNAL" in doc, (
        "the pinned/not-pinned boundary in this module's docstring must name item 5's "
        "EXTERNAL production share as the only §5 clause outside the claim — items "
        "5–7 are pinned by "
        "`test_section_fives_numbered_limits_five_to_seven_state_the_figures_the_"
        "artifact_holds`, so a boundary that lumps items 1–7 together understates "
        "the coverage and invites the next lane to drop these pins")


def test_every_declared_post_run_edit_is_named_in_the_readme(artifact):
    """A provenance ledger the doc under-reports is the defect this PR exists to close.

    ⛔ MEASURED: `post_run_edits` carried TWO entries (`cleanup`, `arms`) while README §6
    said "one block was inserted by hand" and named only `cleanup`. The `arms` entry was
    added by the round-1 fix to the `door_note` prose, after §6's paragraph was written —
    so the document told a reader the only divergence from a reproduction was `cleanup`,
    when a reproduction would also carry the OLD, FALSE note on three arms.

    Asserting the ledger and the prose agree is cheap; the failure it prevents is
    expensive, because §6 is what a reader trusts when deciding whether to re-run.
    """
    edits = artifact["post_run_edits"]
    flat = " ".join(README.read_text().split())
    for edit in edits:
        assert f"`{edit['field']}`" in flat, (
            f"`post_run_edits` declares a hand edit to {edit['field']!r}, but README §6 "
            f"never names it — the ledger says {len(edits)} edit(s) "
            f"({[e['field'] for e in edits]}), and the document must not under-report it")
    # The count, stated in prose, must not contradict the ledger either. "four" is
    # written out in §6's lead sentence; a rewording that drops it is fine as long as
    # no OTHER number is claimed, so only the digit-word for a WRONG count is refused
    # — CASE-INSENSITIVELY, because `**Four**` reds nothing otherwise (measured).
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    n = len(edits)
    low = flat.lower()
    for wrong_n, word in words.items():
        if wrong_n == n:
            continue
        assert f"**{word}** things were done by hand" not in low, (
            f"README §6 claims **{word}** hand edits; the artifact declares {n} "
            f"({[e['field'] for e in edits]})")

    # ⛔ AND THE LEDGER ITSELF IS A DIVERGENCE A REPRODUCTION SHOWS (round 18, F3).
    # The four declared edits describe themselves; they cannot describe the block that
    # declares them, and that block is hand-added too: the tool at `code_head` has no
    # `post_run_edits` builder at all (`git show <code_head>:tools/capstone_12186_recall.py
    # | grep -c post_run_edits` -> 0, measured), so a reproduction's artifact carries no
    # ledger. §6's reproduction paragraph said "four things" and listed four deltas,
    # which reads as the complete set of differences from a reproduction when it is
    # four-of-five. The word `five` is NOT the fix (§6 leads with the LEDGER's count),
    # so the fifth thing is named in the paragraph that enumerates the deltas.
    # ⛔ THE PHRASE, NOT THE WORD, AND ON A WHITESPACE-NORMALISED BLOCK (rounds 18-19,
    # both measured). `"post_run_edits" in repro` stayed GREEN through a mutation that
    # deleted this clause, because the next sentence names the field again. And a
    # FLATTENED multi-word phrase is a FALSE RED: `_flat` removes spaces but not
    # newlines, so moving one source line break (`... post_run_edits` / `ledger either`)
    # left the rendered text identical and redded the pin — `_phrase_re` is the file's
    # one whitespace-tolerant form, and a false red is as bad as a false pass.
    repro_block = (" ".join(_block(README.read_text(), "yields the same numbers").split())
                   .replace("`", "").replace("*", ""))
    assert re.search(_phrase_re("no post_run_edits ledger"), repro_block), (
        "§6's reproduction paragraph must state that a reproduction carries NO "
        "`post_run_edits` LEDGER — the four declared edits cannot declare the block that "
        "declares them, so an enumeration of four reads as the complete set of "
        "divergences when it is four-of-five. A bare `post_run_edits` mention is not "
        "the claim: the paragraph names the field again in a different sentence")


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


def _block(text: str, marker: str) -> str:
    r"""The same BLOCK as `_paragraph`, but UNFLATTENED — spaces and newlines intact.

    ⛔ `_flat` deletes every space, and that is exactly what a POLARITY check cannot
    survive: `_phrase_re` is whitespace-tolerant (`\s+` between words) and `_negates`
    reads a privative's distance from its phrase, so a flattened block matches neither
    — `intoanattach` contains no `\s` at all. Use `_paragraph` for a token/count
    assertion and this for a phrase/polarity one (it is also what `_negates`
    documents: "matched on the unflattened sentence where `\\b` survives").
    """
    blocks = [b for b in text.split("\n\n") if marker in " ".join(b.split())]
    assert len(blocks) == 1, (
        f"expected exactly one block mentioning {marker!r}, got "
        f"{len(blocks)}")
    return blocks[0]


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


def _phrase_mentions(text: str, phrase: str) -> bool:
    """`phrase` in `text`, WHITESPACE-TOLERANT and DIGIT-BOUNDED at both ends.

    ⛔ THE TWO WAYS A `"..." in prose` PIN IS EVADED, and this closes both at once.
    Whitespace: the README is LINE-WRAPPED, so a literal `in` misses a sentence that
    spans two source lines (the sibling pin at `test_the_two_restated_prose_figures_
    are_pinned...` documents this). Digits: a bare substring sees `14 of its 300`
    INSIDE `114 of its 300` — measured, the whole §2a pair stayed green after a
    leading `1` was prepended, and the same shape applied to `35 of 300 excluded`,
    `±0.33 pts` -> `±10.33 pts`, `30 % / 10 %` -> `130 % / 10 %` and `18/6` ->
    `118/6`. `_mentions` cannot help: it is defined on `_flat`, which deletes the
    spaces a multi-word phrase needs. This is `_phrase_re` (the file's one
    whitespace-tolerant form) wrapped in the digit lookarounds `_mentions` uses.
    """
    return re.search(rf"(?<!\d){_phrase_re(phrase)}(?!\d)", text) is not None


def _negates(text: str, phrase: str) -> bool:
    """`phrase` occurs in `text` behind a PRIVATIVE, i.e. the claim is inverted.

    ⛔ A flattened token cannot see polarity, and that is not a hypothetical: `_flat`
    deletes the spaces, so `is no longer visible on the write path` still CONTAINS
    `visibleonthewritepath`, and `no longer too small to call a win` still CONTAINS
    `toosmalltocallawin` — both measured GREEN through `_mentions` before this
    helper existed. `not a decisive win` likewise contains the `else`-branch token.
    So the SURFACE words are `_mentions`' business and the POLARITY is this
    function's, matched on `_clause_raw`'s unflattened sentence where `\\b` survives.

    The window is deliberately TIGHT (at most three words between the privative and
    the phrase). A wider one false-reds: in the rerank clause the unrelated
    `not visible to a user` sits ~24 words before `too small to call a win`, and a
    loose `not .. phrase` match would red a correct sentence — a false red is as bad
    as a false pass (see `_clause`'s docstring).

    ⛔ The PRIVATIVE set is wider than the first cut's four words, and that matters
    most on the RERANK clause, which has no adjacency second net: its positive check
    is `\b<phrase>\b` alone, so this helper is the ONLY thing between the record and
    an inverted significance verdict. Measured GREEN through the four-word set:
    `isn't too small to call a win` (the CONTRACTED spelling of the very privative
    enumerated here — a contraction attaches to its host word, so `n't` cannot carry
    a leading `\b`) and `far from too small to call a win`. Both are covered now at
    window 0, so the tight window the false-red note protects is NOT widened.
    """
    phrase_re = _phrase_re(phrase)
    for privative in _PRIVATIVES:
        if re.search(rf"{privative}\s+(?:\w+\s+){{0,3}}{phrase_re}", text):
            return True
    return False


def _phrase_re(phrase: str) -> str:
    """`phrase` as a WHITESPACE-TOLERANT regex — one normalised form, used everywhere.

    ⛔ The first cut built the positive assertion from LITERAL SPACES while
    `_negates` built its own from `\\s+`, and that asymmetry produced a FALSE RED on a
    correct sentence (measured): `too small to  call a win` — one extra space,
    identical rendering — redded as though the verdict had vanished, and so would a
    reflow that left a DOUBLED SPACE at the line break (the abstract is pre-joined with
    single spaces before any clause is read, so a PLAIN mid-line wrap was never a red —
    measured at `dc8b08bd^`. This sentence first claimed the plain wrap too, which the
    guard never covered; corrected here because an unmeasured claim inside a pin is
    exactly what this file exists to catch). A false red is as bad as a false pass, so
    both sides are now derived from this one function.
    """
    return r"\s+".join(re.escape(word) for word in phrase.split())


#: The privatives that INVERT a claim, as regexes. Split in two because a CONTRACTION
#: (`isn't`, `don't`) attaches to its host word and therefore has no leading `\b` to
#: anchor on — `\bn't` never matches, measured.
#:
#: ⛔ The contraction is CODEPOINT-AGNOSTIC on purpose (`n[^\sa-z0-9]{1,2}t`), not a literal
#: ASCII apostrophe. LLM output and smart-quote autocorrect spell the clitic with every
#: codepoint that looks like an apostrophe (U+2019, U+FF07, U+02BC …), and a negator the
#: pattern cannot see is a negator it fails to guard — measured GREEN through a literal
#: `n't` for `isn’t too small to call a win`, on the one clause with no adjacency second
#: net. This is not a new rule: `tortoise/extractor_v2.py`'s `_CLITIC_RE` states the same
#: shape
#: (`_CLITIC_RE = r"n[^\w\s]{1,2}t$"`) with the same rationale, and
#: `tests/test_never_across_fold_5080.py::test_a_clitic_in_any_apostrophe_spelling_negates`
#: pins 25 apostrophe codepoints for it (26 parametrized cases — U+02BC is listed twice).
#: ⛔ The separator class is `[^\sa-z0-9]` — not `[^\w\s]` — and the difference is
#: measured, not stylistic: Unicode classifies 12 of the apostrophe codepoints this repo
#: enumerates (U+02B9, U+02BB, U+02BD–U+02C1, U+02C8, U+02CA–U+02CC, U+02D0) as
#: MODIFIER LETTERS, i.e. as `\w`, plus U+02BC and U+A78C as letters — so a
#: `[^\w\s]` class silently missed 14 of the 25. `_CLITIC_RE` reaches them only
#: because its translate table canonicalises every one to `'` BEFORE `_CLITIC_RE` runs; a
#: prose pin has no such pre-pass. Excluding only ASCII alphanumerics and whitespace
#: covers ALL 25 codepoints in that inventory (verified), and still refuses the innocent
#: shapes (`print`, `count`, `want` — no separator; `not`/`no` — no `t` after).
_PRIVATIVES: tuple[str, ...] = (
    r"\b(?:not|never|no\s+longer|no\s+more|far\s+from|nowhere\s+near"
    r"|by\s+no\s+means|cannot)\b",
    r"n[^\sa-z0-9]{1,2}t\b",
)


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

    #: ⛔ The `rules` row — the one that justifies the pinned query set at all
    #: (round 11, F3). `_assert_rows_cover` only proves the row EXISTS; its cell was
    #: never read, so `imported, not re-implemented` -> `re-implemented here` and a
    #: dropped `/ `_interleave`` both stayed GREEN (measured) while the docstring
    #: claims §1's provenance is "recomputed from the committed artifact". The value
    #: IS in the pin file, so it is derived: the harness module name plus every
    #: imported symbol, and the refusal that says they were not rewritten.
    rules_pin = json.loads(PIN.read_text())["rules"]
    rules = _flat(_one_row(rows, PROVENANCE_ROW_MARKERS["rules"], "rules")[1])
    _assert_mentions(
        rules,
        [rules_pin["harness"].rsplit("/", 1)[-1].removesuffix(".py"),
         *rules_pin["imported"],
         "imported", "notre-implemented"],
        "§1 rules row")

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

    #: ⛔ THE MASTHEAD, which is a SECOND quotation of two artifact values (round 11,
    #: F4). It sat in neither the PINNED list nor the NOT-PINNED enumeration, so a
    #: reader applying that enumeration concluded it was covered, and the document
    #: could publish two different digests for the same file — the exact
    #: second-quotation-masks-the-first failure this file already fixed for the mint
    #: rate. Both figures are in the artifact; both are pinned against the masthead
    #: region specifically, so §1's copy cannot stand in for a drifted one here.
    #: Lower-cased because the masthead capitalises the `Code head:` label while the
    #: token is the artifact value; `_flat` preserves case, so the surface is folded
    #: rather than the token spelled two ways.
    masthead = _paragraph(README.read_text(), "**Instrument:**").lower()
    _assert_mentions(
        masthead,
        (f"sha256{pin['sha256'][:8]}…",
         f"codehead:{artifact['code_head'][:10]}"),
        "the README masthead")


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
         "notvisibletoauser",
         #: ⛔ THE SIGNIFICANCE VERDICT, derived from the artifact's own `p` rather
         #: than typed here (round 11, F2). Every FIGURE in this clause was pinned
         #: and the judgement built on them was not: `too small to call a win at
         #: p = 0.25` -> `a decisive win at p = 0.25` stayed GREEN (measured), which
         #: is the one claim the record makes about a p = 0.25 result — inverted
         #: with the numbers untouched. Two NON-OVERLAPPING tokens on purpose:
         #: `awin` would be satisfied by `toosmalltocallawin`'s own tail, so the
         #: sub-threshold branch names a phrase that cannot appear in the other.
         "toosmalltocallawin" if rerank["p"] >= 0.05 else "adecisivewin"),
        "the abstract's rerank clause")

    #: ⛔ AND THE POLARITY (round 12), for the same reason as the `#12304` clause: the
    #: significance JUDGEMENT is the claim here, and `no longer too small to call a
    #: win` / `not a decisive win` both stay GREEN through a flattened token (measured).
    #: The phrase is chosen by the artifact's own `p`, so the guard follows the
    #: measurement instead of freezing today's verdict.
    rerank_raw = _clause_raw(paragraph, "#12343").replace("*", "")
    rerank_phrase = (r"too small to call a win" if rerank["p"] >= 0.05
                     else r"a decisive win")
    assert re.search(rf"\b{_phrase_re(rerank_phrase)}\b", rerank_raw), (
        f"the abstract's rerank clause no longer states the significance verdict the "
        f"artifact's p = {rerank['p']} implies ({rerank_phrase!r}): {rerank_raw!r}")
    assert not _negates(rerank_raw, rerank_phrase), (
        f"the abstract's rerank clause now NEGATES its significance verdict, which is "
        f"the one claim the record makes about a p = {rerank['p']} result: "
        f"{rerank_raw!r}")

    #: ⛔ THE THREE-LEVER SUMMARY SENTENCE IS CHECKED BY `_assert_headline_states`
    #: AT THE END OF THIS TEST, AND BY NOTHING HERE (round 20). A loop used to sit on
    #: this line asserting `LEVER_COLUMNS[key][1].startswith(want)` for a `want` taken
    #: from a literal tuple three lines above it — two module literals compared to each
    #: other, which no edit to the README can falsify, under a comment claiming it
    #: stopped `one … is visible` -> `two … are visible`. The claim is real and IS
    #: enforced: by the artifact-derived `_door_verdict_counts` call below. The dead
    #: loop is removed rather than kept as decoration.
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
         f"{mint['shipped']['rate'] * 100:.1f}%",
         #: ⛔ THE SURFACE, not only the two rates (round 11, F1). The rates alone
         #: leave the clause's claim free: `is visible on the **write path**` ->
         #: `is visible to a **user**` kept both rates and stayed GREEN (measured),
         #: shipping a false user-capability claim for the one lever the record says
         #: is write-path ONLY — contradicting §4's WHOLE-pinned `ON (write path)`
         #: row and the same paragraph's own `one is only visible on the write path`.
         #: This is the token the sibling clauses already carry (`notvisibletoauser`,
         #: `offbydefault`); its absence here was the gap, not its form.
         "visibleonthewritepath"),
        "the abstract's `#12304` clause")

    #: ⛔ AND THE POLARITY (round 12). The flattened token above proves the words are
    #: present; it cannot prove they are not NEGATED, because `_flat` deletes the
    #: spaces that separate a privative from them. Measured GREEN before this:
    #: `is visible on the **write path**` -> `is no longer visible on the **write
    #: path**` (and `is not visible …`), which makes the record say the shipped
    #: `#12304` fold does NOTHING while §4's `YES on the write path` row and the same
    #: paragraph's `one is only visible on the write path` stay pinned — so the
    #: contradiction is invisible. The RAW sentence is where `\b` survives, so the
    #: verb phrase must be ADJACENT (`is\s+visible`, nothing between) and no privative
    #: may LEAD it (`no longer is visible on the write path` is valid English and
    #: would pass the adjacency check alone).
    write_path_raw = _clause_raw(paragraph, "#12304").replace("*", "")
    assert re.search(rf"\b{_phrase_re(r'is visible on the write path')}\b",
                     write_path_raw), (
        f"the abstract's `#12304` clause no longer states, ADJACENTLY, that the fold "
        f"is visible on the write path — a privative or qualifier inserted between "
        f"those words leaves the flattened token satisfied: {write_path_raw!r}")
    assert not _negates(write_path_raw, r"is visible on the write path"), (
        f"the abstract's `#12304` clause now NEGATES the write-path visibility, so the "
        f"record claims the shipped fold does nothing: {write_path_raw!r}")


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

    # ⛔ AND THE SENTENCE'S DIRECTION, NOT ONLY ITS TWO NUMBERS (round 18, F4,
    # measured). `192 of 411` is the ATTACH bucket — `mint_rate.shipped.attached` —
    # so the verb *attach* IS the claim; a bare token count cannot see it. Measured:
    # `into an attach` -> `into a mint` left every pin above untouched and the suite
    # at `48 passed`, while the sentence then stated the OPPOSITE of §3b's own row
    # (`shipped matcher ... 397 | 192 | 0.5328`: 192 attached, 219 minted) and of §4's
    # `1.0000 -> 0.5328`. This test's title claims the narrative "cannot contradict
    # its own tables" — for this restatement it could, which is the same one-word
    # inversion the abstract's clauses already close with `_negates`.
    attached = mint["shipped"]["attached"]
    assert attached != mint["pairs"] - attached, (
        "the two buckets are equal, so `attach` and `mint` are not distinguishable "
        "in this sentence and its direction cannot be pinned")
    sentence = _block(README.read_text(), f"{attached} of {mint['pairs']}")
    # ⛔ THE CLOSED FORM IS NOT ASSERTED HERE, AND THAT IS DELIBERATE (round 20). Two
    # cuts tried to, and both were INERT. The first was `attached ==
    # int(tokens[2].split("of")[0])` — a SELF-COMPARISON, since `tokens[2]` is built
    # from `mint['shipped']['attached']` twenty lines above, so no mutation of the
    # artifact or the README could fail it. The second was
    # `re.search(_phrase_re(marker), sentence)`, which is ENTAILED by `_block`'s own
    # precondition: `_block` selects the block BY that marker (`marker in
    # " ".join(b.split())`) and `_phrase_re` joins the same words with `\\s+`, a
    # superset of what `str.split()` splits on — so no document edit that reaches this
    # line can falsify it. The figure is read by the DOCUMENT-WIDE uniqueness assertion
    # above (`tokens[2]` occurs exactly once, `_mentions`-checked); the three assertions
    # below are what read the DIRECTION, and they bite.
    assert re.search(_phrase_re("into an attach"), sentence), (
        f"the `{tokens[2]}` restatement no longer says the duplicates become an "
        f"ATTACH — the figure is `mint_rate.shipped.attached`")
    assert not re.search(_phrase_re("into a mint"), sentence), (
        f"the `{tokens[2]}` restatement now says those duplicates are MINTED, which "
        f"contradicts §3b's own row: {sentence!r}")
    assert not _negates(sentence, "into an attach"), (
        f"the `{tokens[2]}` restatement NEGATES the attach, so the record claims the "
        f"fold did not attach the duplicates it counts: {sentence!r}")
