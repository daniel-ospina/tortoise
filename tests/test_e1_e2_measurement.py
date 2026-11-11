"""#9076 — the E1+E2 one-run measurement: its corpus, its census, and its honesty gates.

**Why a test file for a probe.** The probe
(`docs/evidence/7949-capstone/probes/e1_e2_probe.py`) is the only artifact that
puts E1 and E2 through the shipped pipeline on one corpus in one process, and the
log it writes is a CLAIM about a product promise. Nothing in the tree would fail
if that claim quietly lost its denominator, scored itself against a threshold
chosen after the run, or reported a counterfactual arm as the product's result.
So the pins here are:

1. **The corpus is reproducible from its two named sources** — a silent edit to
   ``e1e2_corpus.json`` fails here rather than moving a number in a log.
2. **The census is arithmetic, not narrative** — numerator/denominator/share are
   checked against each other on a synthetic payload, including the empty case.
3. **The measured numbers are PINNED.** The E1 finding this lane delivers is that
   the shipped arm moves the share by exactly 0.0 — the discards it does make are
   all downgraded by ``apply_vet``'s Layer-1 referential guard. Pinning 0.0 is what
   makes a future movement visible: if the arbiter seam ever gets a production
   caller, this file forces the measurement to be re-taken on purpose (the same
   discipline ``tests/test_salience_labels_4917.py`` applies to 9/15).
4. **The structural checker fails on every dishonest shape** — one test per
   mutation, so the gate cannot pass by omission.
5. **No criterion is PASS without a frozen threshold**, the SCORED arm is
   never a counterfactual, and a threshold the run MEETS cannot green it while a
   control is red (the probe's `green = …` conjunction is otherwise unpinned).
6. **Collecting this file must not mutate the interpreter.** The probe is imported
   at collection time, so a process-global `logging.disable` in it would silence
   every `caplog` assertion in the files that run after this one (#9110 review P1).
7. **The committed log is FROZEN, not re-taken** — `e1-e2.log` predates the
   pre-registration, which grandfathers it on five bounds (`PRE-REGISTRATION.md`
   §0/§7 item 2), one of them being that its content may not be modified at or
   after the registration's add-commit. So it is pinned as a **constant digest**
   (`test_the_committed_log_is_frozen_and_stays_unscored`), never as
   "what the probe prints" — that older pin's remedy (re-take the log) is
   forbidden, and it was what made a required check unsatisfiable (#11746). The
   probe's printed text is pinned against the probe's **own** output instead.

DB-free: the pipeline runs with a scripted model, ``sdk=None``, no network.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util as _ilu
import json
import logging
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROBE_PATH = (_REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "probes"
               / "e1_e2_probe.py")
#: The two SIBLING readers of `denominator_floors.e2b_surviving_units`. Both are
#: read here to prove they score the same population the binding does — and both
#: are read as an AST, never as text (see `_call_keyword_values`).
_MEASURE_PATH = (_REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "probes"
                 / "capstone_measure.py")
_SCORES_PATH = (_REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "probes"
                / "e1_e2_scores.py")
_CORPUS_DIR = _REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "corpus"
_CORPUS = _CORPUS_DIR / "e1e2_corpus.json"
#: #9507's committed record — the artifact a REAL `--prereg` is handed. Read from
#: disk so a pin about its floors cannot drift away from the record it describes.
_PREREG_ARTIFACT = (_REPO_ROOT / "docs" / "evidence" / "7949-capstone"
                    / "preregistration.json")

#: The run's corpus, repo-relative — what a REAL pre-registration must pin before
#: its bar can be scored over this population (#9076). The artifact shape is
#: #9507's ``capstone-preregistration/v1``; the pins here are the ones the probe
#: compares, and the floor is set under the corpus's 25 Objects so the helper is
#: about binding, not about the floor (a below-floor case is its own test).
_CORPUS_REL = "docs/evidence/7949-capstone/corpus/e1e2_corpus.json"


def _bound_artifact(threshold: float = 0.05) -> dict:
    """A pre-registration that is BOUND to this run: corpus pin + a floor it meets.

    Every scored-run pin below builds its artifact through this helper, because a
    threshold with no corpus pin is not a bar for this population (#9076) — the
    pre-fix shape scored it anyway.
    """
    return {"e1": {"min_share_reduction": threshold},
            "corpus": {"path": _CORPUS_REL},
            "denominator_floors": {"e1_objects": 20}}
_LOG = _REPO_ROOT / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"

#: `e1-e2.log`'s bytes, pinned as a CONSTANT (#11746 review P2). The *reproduction* pin is
#: gone — it demanded a re-take the pre-registration forbids — but a *content* pin is a
#: different thing and stays satisfiable forever: this log may never change again, so its
#: digest is a constant rather than a re-take target.
#:
#: What it adds, precisely — because two gates already guard this file and neither is this
#: one. The CENSUS CHECKER reconciles the log with ITSELF, so it only sees an edit that makes
#: two of the log's own lines disagree: editing a free-text/`note=` span
#: (`split-implemented=no note=…`) leaves it reporting "structurally sound" (MEASURED). The
#: ORDERING gate is content-blind and reds ANY commit that touches the log, so it covers the
#: committed case — but it reads COMMITS only, and answers `unprovable` in a shallow checkout.
#: The digest is therefore what catches the edit that is not yet a commit, and the one seen
#: from a checkout with no history to read.
_FROZEN_LOG_SHA256 = "9e44d4f13d716e063bb50bc4305ba14e5e8b81709275e914a44eac1e61de5f37"

sys.path.insert(0, str(_REPO_ROOT))
from tools import capstone_preregistration_check as prereg_check  # noqa: E402
from tools import e1_e2_census_check as check  # noqa: E402


def _load(path: Path, name: str):
    """Load a hyphenated-directory module by path — the probes' established pattern."""
    spec = _ilu.spec_from_file_location(name, str(path))
    assert spec and spec.loader
    module = _ilu.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load(_PROBE_PATH, "e1_e2_probe")
builder = _load(_CORPUS_DIR / "build_e1e2_corpus.py", "build_e1e2_corpus")
#: The ONE owner of "is this sha a commit in THIS checkout" — a shallow clone cannot
#: answer, so it answers `unprovable` instead of mutating a legitimate log into a red
#: required check (#11689 review round 8). Reused rather than re-derived.
_LEDGER = _load(_REPO_ROOT / "tools" / "capstone_ledger.py", "capstone_ledger")


# ── 1. the corpus ─────────────────────────────────────────────────────────


def test_committed_corpus_matches_a_fresh_build():
    """A hand-edited corpus would move the denominator without moving any rule."""
    committed = _CORPUS.read_text(encoding="utf-8")
    fresh = json.dumps(builder.build(), indent=1, sort_keys=True,
                       ensure_ascii=False) + "\n"
    assert committed == fresh, (
        "e1e2_corpus.json no longer matches build_e1e2_corpus.py — rebuild it "
        "with `python docs/evidence/7949-capstone/corpus/build_e1e2_corpus.py` "
        "and say why the population changed"
    )


def test_corpus_populations_are_the_two_named_sources():
    corpus = probe.load_corpus(_CORPUS)
    assert len(corpus["objects"]) == 25, (
        "the Object population is the 25 owner-confirmed audit names")
    audit = [p for p in corpus["points"] if p["provenance"].startswith("prod-graph-audit")]
    planted = [p for p in corpus["points"] if p.get("must_keep")]
    assert len(audit) == 25, "each audited Object keeps the statement it was minted from"
    assert len(planted) == 90, (
        "the anti-gaming control is the versioned corpus's 90 planted units")
    assert all(p["source"].startswith("tests/eval/write_path/") for p in corpus["points"])
    # the four names a LATER owner ruling (#1026) makes must-keep are flagged, so
    # no arm can read the audit set as a drop list (#4917's own warning).
    flagged = {o["name"] for o in corpus["objects"] if o["keep_object_ruling"]}
    assert flagged == {"guard 2", "_TOOL_BY_NAME", "daniel-ospina/agent-infra",
                       "_capture_cost_props"}, flagged


def test_no_arm_discards_a_keep_object_name():
    """#1026 rules four audited names MUST-KEEP; dropping one would reverse it."""
    corpus = probe.load_corpus(_CORPUS)
    embed = probe.build_embed_list(corpus)
    _minted, meta = probe.mint_skip_transform(embed)
    keep = {o["name"] for o in corpus["objects"] if o["keep_object_ruling"]}
    assert not (set(meta["dropped_objects"]) & keep), (
        "the counterfactual mint-time arm dropped a #1026 keep-object")


# ── 2. the census is arithmetic ───────────────────────────────────────────


def test_find_values_finds_the_literals_the_carve_out_exists_for():
    text = ("it hit 4.2 seconds yesterday, the p95 was 62 percent, a ten-minute "
            "TTL, and forty rows per page on the October 5 release")
    found = {lit for _cls, lit in probe.find_values(text)}
    assert "4.2 seconds" in found
    assert "62 percent" in found
    assert "ten-minute" in found
    assert "forty rows" in found
    assert "October 5" in found


def test_find_values_returns_nothing_for_prose_without_values():
    assert probe.find_values("the plan is fine and the review is pending") == []


def test_claim_units_splits_sentences_and_semicolons():
    assert len(probe.claim_units("the cache is warm")) == 1
    assert len(probe.claim_units("the cache is warm. the queue is flat")) == 2
    assert len(probe.claim_units("first do this; then do that")) == 2


def test_census_reports_the_share_as_both_counts():
    corpus = {"points": []}
    payload = {"entities": [{"name": "the thing"}, {"name": "the other"},
                            {"name": "Maya Chen"}],
               "points": [{"content": "it hit 4.2 seconds"}]}
    cen = probe.census({"payload": payload, "vet": {}}, corpus)
    assert (cen["objects_the_prefix"], cen["objects_total"]) == (2, 3)
    assert cen["share"] == pytest.approx(2 / 3)
    assert cen["state_value_count"] >= 1


def test_the_probe_asserts_one_pre_gate_population_across_the_gate_arms():
    """#9110 review P2: the claim was PRINTED, never asserted.

    `before`, `after-vet-shipped` and `after-vet-arbiter` differ only inside the
    gate; if a future edit gives one of them its own candidate list, the delta is
    no longer attributable to the gate and the run must fail — not print three
    shas for a reader to notice. The mint-skip arm is excluded by construction.
    """
    corpus = probe.load_corpus(_CORPUS)
    embed = probe.build_embed_list(corpus)
    minted, _meta = probe.mint_skip_transform(embed)
    arms = [("before", embed, False, None, False, "n/a"),
            ("after-vet-shipped", embed, True, None, False, "vet-discard"),
            ("after-vet-arbiter", embed, True, lambda c, n: {"verdicts": []}, True,
             "vet-discard"),
            ("counterfactual-mint-skip", minted, True, None, True,
             "mint-time-transform")]
    assert probe.assert_one_gate_population(arms) == probe._sha(embed)
    drifted = json.loads(json.dumps(embed))
    drifted["points"] = drifted["points"][1:]
    arms[2] = (arms[2][0], drifted, *arms[2][2:])
    with pytest.raises(AssertionError, match="one pre-gate population"):
        probe.assert_one_gate_population(arms)


def test_the_log_prints_the_value_literals_it_counts(run):
    """#9110 review P2: `values=42` was a count nobody could audit.

    The docstring and ``E1-E2.md`` limit 5 promise a reader can audit the matches
    instead of trusting the count; the literals lived only in the optional
    ``--json-out`` file, which was not part of the bundle. So the log prints them,
    class and match, one line per arm — including the overlap, which is the whole
    reason the figure is a count of MATCHES and not of distinct values.
    """
    lines = [ln for ln in run["text"].splitlines()
             if " metric=state_value_literals " in ln]
    assert len(lines) == 4, lines
    for ln in lines:
        matches = json.loads(re.search(r"matches=(\[.*\])$", ln).group(1))
        assert matches, ln
        assert all(len(m) == 2 and all(isinstance(x, str) for x in m)
                   for m in matches), ln
    before = json.loads(re.search(
        r"matches=(\[.*\])$",
        next(ln for ln in lines if ln.startswith("arm=before "))).group(1))
    assert len(before) == MEASURED_STATE_VALUES
    assert ["percent", "62 percent"] in before
    assert ["count", "62"] in before, (
        "the overlapping match is the disclosure: the same literal counts under "
        "two classes, so the total is matches, not distinct values")


def test_census_never_divides_by_zero_on_an_empty_graph():
    cen = probe.census({"payload": {"entities": [], "points": []}, "vet": {}}, {"points": []})
    assert cen["objects_total"] == 0
    assert cen["share"] is None


# ── 3. the measurement, pinned ────────────────────────────────────────────

#: MEASURED at the commit this file landed on. If any of these move, re-take the
#: measurement and update them on purpose — do not let the finding drift silently.
MEASURED_BEFORE_SHARE = 0.60          # 15 of the 25 audited Objects are `the <X>`
MEASURED_SHIPPED_REDUCTION = 0.0      # the shipped arm moves the share by NOTHING
MEASURED_MINT_SKIP_REDUCTION = 0.225  # 9/25 dropped → 6/16 remain `the <X>`
MEASURED_STATE_VALUES = 42            # value MATCHES across 34 Points (patterns
                                      # overlap by design: `62 percent` also
                                      # matches `count` for `62`)
MEASURED_REAL_FACTS = 90              # planted must-keep units, both sides
#: The discriminating control (``E1-E2.md`` limit 6). Same arm, same corpus, only
#: the references of the 9 positively-classified definite descriptions stripped:
#: the discards LAND, which is what makes the 0.0 pp a CORPUS property.
MEASURED_CONTROL_STRIPPED = 9
MEASURED_CONTROL_REMOVED = 9
MEASURED_CONTROL_SHARE = 0.375        # 6 of 16 Objects remain `the <X>`


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """One real probe run, reused by every pin below (≈2.5 s for four arms)."""
    import io
    from contextlib import redirect_stdout

    out = tmp_path_factory.mktemp("e1e2") / "e1-e2.json"
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--json-out", str(out)])
    assert out.exists(), "--json-out must write the machine-readable census"
    return {"text": buf.getvalue(), "exit": code}


def test_the_run_measures_instead_of_scoring_when_no_prereg_is_frozen(run):
    """Measured numbers are evidence; a verdict needs a frozen threshold (#9075)."""
    assert run["exit"] == 3, run["text"]
    assert "result=UNPREREGISTERED" in run["text"]
    assert "prereg=UNPREREGISTERED" in run["text"]


def test_e1_share_is_the_pinned_number_with_both_counts(run):
    line = next(ln for ln in run["text"].splitlines()
                if ln.startswith("arm=before metric=objects "))
    assert "numerator=15" in line and "denominator=25" in line
    assert f"share={MEASURED_BEFORE_SHARE:.6f}" in line


def test_the_shipped_arm_moves_the_share_by_exactly_zero(run):
    """THE FINDING: the shipped gate cannot move E1, and this is why.

    ``TORTOISE_VET=1`` + ``TORTOISE_VALUE_GATE=1`` fires the identifier-only
    predicate on 3 candidates and ``apply_vet`` Rule 2 downgrades all 3 to KEEP —
    each is referenced by the statement it was minted from, and dropping the
    Object would break Layer-1 referential integrity. So volume is unchanged. The
    cause is structural (the reference), not a threshold.
    """
    before = next(ln for ln in run["text"].splitlines()
                  if ln.startswith("arm=before metric=objects "))
    after = next(ln for ln in run["text"].splitlines()
                 if ln.startswith("arm=after-vet-shipped metric=objects "))
    assert re.search(r"share=(\S+)", before).group(1) == \
        re.search(r"share=(\S+)", after).group(1)
    assert "observed=NO_REDUCTION" in run["text"]
    assert "vet-downgrades count=6" in run["text"]  # 3 per pass (S2 + union)
    assert "kept (Layer-1 referential integrity)" in run["text"]


def test_the_counterfactual_arms_are_reported_and_never_scored(run):
    assert "scored_arm=after-vet-shipped" in run["text"]
    for arm in ("after-vet-arbiter", "counterfactual-mint-skip"):
        line = next(ln for ln in run["text"].splitlines()
                    if ln.startswith(f"counterfactual arm={arm} "))
        assert "note=NOT SCORED" in line
    mint = next(ln for ln in run["text"].splitlines()
                if ln.startswith("counterfactual arm=counterfactual-mint-skip "))
    assert f"reduction={MEASURED_MINT_SKIP_REDUCTION:.6f}" in mint


def test_e2_controls_do_not_move_on_any_arm(run):
    """The anti-gaming property: E1 may not be bought with facts or values."""
    line = next(ln for ln in run["text"].splitlines() if ln.startswith("e1-control "))
    assert f"before_real_facts={MEASURED_REAL_FACTS}" in line
    assert f"after_real_facts={MEASURED_REAL_FACTS}" in line
    state = next(ln for ln in run["text"].splitlines() if ln.startswith("e2-state "))
    assert f"before={MEASURED_STATE_VALUES}" in state
    assert f"after={MEASURED_STATE_VALUES}" in state


def test_atomicity_is_reported_and_split_is_named_as_the_blocker(run):
    assert "split-implemented=no" in run["text"]
    fused = [ln for ln in run["text"].splitlines() if ln.startswith("e2-atomic fused ")]
    assert fused, "the fused Point must be listed, not merely counted"
    assert all("cause=SPLIT-not-implemented" in ln for ln in fused)


def test_the_discriminating_control_separates_the_gate_from_the_corpus(run):
    """#9110 review P2: is the 0.0 pp the GATE, or is it this CORPUS?

    The arbiter arm's "9 discards attempted, all 9 downgraded, 0 removals" is
    arithmetic-identical under two explanations: S2.2 cannot remove a definite
    description, or every Object in this corpus is wired to the statement it was
    minted from, so ``apply_vet`` Rule 2 always fires. The bundle's central
    conclusion (and the mint-time recommendation to #7949) rests on which one, so
    the probe measures it: strip ONLY those 9 references, keep the entities and
    their statements, run the same arm again.

    This is the control *failing* on the pre-fix probe in the only way that
    matters — before it existed there was no measurement, and the doc asserted the
    stronger claim from four arms that cannot distinguish the two cases.
    """
    line = next(ln for ln in run["text"].splitlines()
                if ln.startswith("control variant=unreferenced-definite-descriptions "))
    assert f"stripped_objects={MEASURED_CONTROL_STRIPPED}" in line, line
    assert f"stripped_references={MEASURED_CONTROL_STRIPPED}" in line, line
    assert f"removed={MEASURED_CONTROL_REMOVED}" in line, line
    assert f"share={MEASURED_CONTROL_SHARE:.6f}" in line, line
    assert "objects=16" in line and "the_prefix=6" in line, line
    assert "real_facts=90" in line, (
        "the control must still show E2 unharmed — the point is that the removals "
        "land WITHOUT losing a fact")
    # the contrast: the same arm on the referenced corpus removes nothing
    assert "arm=after-vet-arbiter metric=objects total=25 " in run["text"]
    assert "counterfactual arm=after-vet-arbiter reduction=0.000000" in run["text"]


def test_strip_references_keeps_the_entity_and_drops_only_the_edge():
    """The control's variable must be the REFERENCE, not the candidate list."""
    embed = {"entities": [{"name": "the thing"}, {"name": "Maya Chen"}],
             "events": [], "points": [
                 {"content": "Maya Chen owns the thing",
                  "about_entities": ["the thing", "Maya Chen"]}],
             "operators": [], "chain_notes": [], "link_before_create": []}
    variant, stripped = probe.strip_references(embed, {"the thing"})
    assert stripped == 1
    assert [e["name"] for e in variant["entities"]] == ["the thing", "Maya Chen"]
    assert variant["points"][0]["about_entities"] == ["Maya Chen"]
    assert variant["points"][0]["content"] == "Maya Chen owns the thing"
    assert embed["points"][0]["about_entities"] == ["the thing", "Maya Chen"], (
        "the control must not mutate the measured candidate set in place")


def test_coverage_honesty_names_the_classes_with_no_detector(run):
    line = next(ln for ln in run["text"].splitlines() if ln.startswith("coverage rows="))
    assert "positively_classified=9" in line
    assert "in_scope=15" in line
    undetected = re.search(r"undetected_classes=(\S+)", line).group(1).split(",")
    assert "symbol_name" in undetected and "repo_slug" in undetected
    # the value gate DOES reach two classes the dd predicate cannot — reported apart
    assert "class=pr_or_issue_number rows=2 dd_positive=0 value_gate=2" in run["text"]
    assert "class=file_path rows=1 dd_positive=0 value_gate=1" in run["text"]


# ── 4. the structural checker — one test per dishonest shape ──────────────


@pytest.fixture(scope="module")
def log_text(run):
    return run["text"]


def test_the_produced_log_passes_the_structural_check(log_text):
    findings, state = check.check(log_text)
    assert findings == []
    assert state["result"] == "UNPREREGISTERED"


def _assert_the_log_sha_is_a_real_object(log_sha: str, state: str) -> None:
    """The OBJECT half of the provenance pin, as a function OF THE ANSWER.

    Split out of the test body so both outcomes are pinnable on any machine, with no
    dependence on which objects THIS clone happens to carry (#11689 review round 10):
    the old inline `assert state == "exists"` could only vary by a foreign ref
    disappearing, which is the defect rather than a mutant any test could run.

    ⛔ `absent` is ACCEPTED, and that is a measurement, not a relaxation. Measured at
    `1891e5244874`: this log's sha (`c2544f54f14`) is NOT an ancestor of `origin/main`
    AND NOT an ancestor of this branch (`git merge-base --is-ancestor` → false for
    both); its only fetched ref is `refs/remotes/origin/feat/9076-tm` — the head of PR
    #9110, MERGED, squash commit `bc547406f255` — and `refs/pull/*` is not fetched by
    a clone. This repo DELETES merged heads (measured: 4 of 5 recently merged PR heads
    have no `refs/heads/*` ref left), so once `feat/9076-tm` is reaped the object is
    reachable from no fetched ref: `cat-file -e` exits 128, the repo is complete, and
    `_commit_state` answers `absent`. So `absent` is a fact about the CLONE's refspec,
    not about the log — and reddening on it makes this pin red on `main` in every fresh
    full clone, for a log `PRE-REGISTRATION.md` §0 forbids anyone from re-taking. An
    assertion whose only remedy a recorded decision forbids is unsatisfiable by
    construction: the same class this PR already removed one field over.

    ⛔ It is NOT a fail-open, because the fabricated-sha catch the object probe used to
    carry is owned by `_FROZEN_LOG_SHA256`: the `sha=` line is INSIDE the bytes that
    digest pins, so `sha=0000…0` (or a force-pushed-away commit) cannot reach here
    without reddening `test_the_committed_log_is_frozen_and_stays_unscored` first.
    `test_the_frozen_digest_owns_the_sha_line` pins that ownership in both directions —
    and the digest, unlike this probe, also runs in the depth-1 CI checkout, where the
    probe answered `unprovable` and caught nothing at all.

    What is LEFT as a verdict here: `unprovable` is a MEASURED answer about a shallow
    checkout and skips; `unmeasurable` (git could not be asked) is not evidence and
    reds, as does any state this predicate cannot classify.
    """
    if state == "unprovable":
        pytest.skip(f"a shallow checkout cannot resolve {log_sha} — unprovable is "
                    f"not absent (tools/capstone_ledger.py::_commit_state)")
    assert state in ("exists", "absent"), (
        f"e1-e2.log names sha={log_sha}, and this checkout answers {state!r} — neither "
        f"'exists' (measured present) nor 'absent' (measured not reachable here) was "
        f"measured, so this is not a verdict about the log")


def test_the_committed_log_names_a_real_commit_and_the_doc_agrees():
    """`sha=` must name a commit, and `Measured at:` must be that same commit.

    #11689 review: re-taking the log with `| tee <tracked log>` truncates the tracked
    file BEFORE the probe asks `measurement_sha()`, so the artifact came out naming
    `<sha>+dirty` — a tree that is not any commit — while E1-E2.md still asserted the
    two agree "by construction". Nothing could red on it, because the reproduction pin
    masks the whole `sha=` line. This is that pin: an evidence log that names no
    commit is not evidence.
    """
    import re

    log_sha = next(ln for ln in _LOG.read_text(encoding="utf-8").splitlines()
                   if ln.startswith("sha=")).split("=", 1)[1]
    assert not log_sha.endswith("+dirty"), (
        f"e1-e2.log names {log_sha!r}, which is not a commit — a tree that is not any "
        f"commit is not evidence. This log is FROZEN (PRE-REGISTRATION.md §0), so the "
        f"remedy is NOT to re-take it: a `+dirty` sha means something overwrote the "
        f"frozen bytes with a fresh probe run. Restore them — "
        f"`git checkout origin/main -- docs/evidence/7949-capstone/e1-e2.log` — and put "
        f"that run in a NEW log, which is what the E1/E2 re-run must do anyway (it must "
        f"postdate the pre-registration). `| tee <a tracked log>` is what produced this "
        f"shape: it truncates the file before the probe asks for its sha"
    )
    assert re.fullmatch(r"[0-9a-f]{40}", log_sha), log_sha
    # ⛔ The GIT-FREE half runs FIRST (#11689 review round 9). The `test` job checks
    # out at depth-1, so the sha below is `unprovable` there and skips — if the
    # doc/log agreement were asserted after that skip, the half of this pin that
    # needs no object database would never run in the required check, while
    # `E1-E2.md` says this test "checks exactly that" the two agree.
    doc = (_LOG.parent / "E1-E2.md").read_text(encoding="utf-8")
    measured = re.search(r"\*\*Measured at:\*\* `([0-9a-f]{7,40})`", doc)
    assert measured, "E1-E2.md states no `Measured at:` commit"
    assert log_sha.startswith(measured.group(1)), (
        f"E1-E2.md's `Measured at:` ({measured.group(1)}) is not the commit the log "
        f"names ({log_sha}) — the two are supposed to agree by construction")
    # #11689 review round 7: the docstring above says "an evidence log that names no
    # commit is not evidence", and this pin only matched 40 hex characters — so
    # `sha=0000…0`, or the sha of a commit a force-push removed, satisfied every
    # assertion while the doc claimed a provenance the gate never checked.
    #
    # ⛔ It cannot be a `git cat-file -e` exit test, and the reason is MEASURED in
    # this repo: the `test` job's only checkout is `actions/checkout@v4` with no
    # `fetch-depth`, so every shard is depth-1 and the object is simply not there —
    # `cat-file -e` exits 128 and a correct tree would red a REQUIRED check. That is
    # the doctrine in `tools/capstone_ledger.py::_commit_state` ("a shallow checkout
    # CANNOT answer", pinned by `test_a_shallow_checkout_is_unprovable_never_absent`),
    # so this uses that one predicate rather than a second owner of it. The object
    # half is `_assert_the_log_sha_is_a_real_object`, whose docstring carries the
    # measurements — this log's sha is NOT an ancestor of this branch, which an
    # earlier revision of this comment wrongly asserted.
    _assert_the_log_sha_is_a_real_object(
        log_sha, _LEDGER._commit_state(log_sha, _REPO_ROOT))


def test_the_frozen_digest_owns_the_sha_line():
    """A fabricated `sha=` is caught by the byte digest, in every checkout.

    #11689 review round 10: the object half of the provenance pin stops reddening on
    `absent` (see `_assert_the_log_sha_is_a_real_object`), so the fabricated-sha catch
    it was carrying — `sha=0000…0`, or a commit a force-push removed — must be
    demonstrably owned by the digest that pins this log's bytes. It is: the `sha=` line
    lives INSIDE those bytes. Pinned in both directions below, and note that unlike the
    object probe this digest runs in the depth-1 CI checkout — where the probe answered
    `unprovable` and caught nothing at all.
    """
    raw = _LOG.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _FROZEN_LOG_SHA256
    sha_line = next(ln for ln in raw.decode("utf-8").splitlines()
                    if ln.startswith("sha=")).encode("utf-8")
    forged = raw.replace(sha_line, b"sha=" + b"0" * 40, 1)
    assert forged != raw
    # the SHA-SHAPE assertions this pin also makes pass a forged 40-hex value …
    import re

    forged_sha = next(ln for ln in forged.decode("utf-8").splitlines()
                      if ln.startswith("sha=")).split("=", 1)[1]
    assert not forged_sha.endswith("+dirty")
    assert re.fullmatch(r"[0-9a-f]{40}", forged_sha)
    # … and the object probe cannot refuse it once no fetched ref reaches it (it
    # accepts `absent` for the REAL sha, so it must accept it for this one too):
    # the DIGEST is what refuses it, below. That is the ownership this test pins.
    assert hashlib.sha256(forged).hexdigest() != _FROZEN_LOG_SHA256, (
        "the digest does not cover the sha= line — then accepting `absent` in the "
        "object probe really would be a fail-open")


@pytest.mark.parametrize("state", ["exists", "absent"])
def test_the_object_half_does_not_depend_on_which_refs_this_clone_has(state):
    """`absent` is a fact about the clone's refspec, not about the log (#11689 r10).

    This log's sha is not on `main`, is not an ancestor of this branch, and is
    reachable only through `refs/heads/feat/9076-tm` (the head of PR #9110) plus
    `refs/pull/*`, which a clone does not fetch. This repo deletes merged heads, so once
    that one is reaped a COMPLETE clone answers `absent` — and an `== "exists"` oracle
    then reds `main` forever, for a log `PRE-REGISTRATION.md` §0 forbids anyone from
    re-taking. An assertion whose only remedy a recorded decision forbids is
    unsatisfiable by construction.

    ⛔ Non-vacuous by construction: before this round the helper asserted
    `state == "exists"`, so the `absent` parameter raises `AssertionError` there.
    """
    _assert_the_log_sha_is_a_real_object("c" * 40, state)


def test_the_object_half_still_refuses_a_state_that_is_not_a_measurement():
    """What is LEFT as a verdict: git could not be asked, or an unknown state.

    `unprovable` is a MEASURED answer about a shallow checkout (the doctrine in
    `tools/capstone_ledger.py::_commit_state`), so it skips; `unmeasurable` is not
    evidence and reds, as does any state this predicate cannot classify.
    """
    with pytest.raises(pytest.skip.Exception):
        _assert_the_log_sha_is_a_real_object("c" * 40, "unprovable")
    with pytest.raises(AssertionError):
        _assert_the_log_sha_is_a_real_object("c" * 40, "unmeasurable")
    with pytest.raises(AssertionError):
        _assert_the_log_sha_is_a_real_object("c" * 40, "bogus")


def test_the_committed_log_passes_the_structural_check():
    findings, _state = check.check(_LOG.read_text(encoding="utf-8"))
    assert findings == [], findings


def test_the_committed_log_is_frozen_and_stays_unscored():
    """This log is GRANDFATHERED — and that is why no reproduction pin applies to it.

    This test used to be `test_the_committed_log_is_what_the_probe_prints`: it
    byte-compared the committed log to the CURRENT probe's output, and its failure
    message told the reader to re-take the log. **That remedy is forbidden**, and the
    pin was therefore unsatisfiable in principle:

    `e1-e2.log` predates the pre-registration (`#9507`), which grandfathers it on five
    bounds (`PRE-REGISTRATION.md` §0/§7 item 2) — one of which is that its content must
    not have been modified at or after the registration's add-commit, because an
    in-place edit can put a measurement number into a grandfathered log after the
    thresholds were written. `tools/capstone_preregistration_check.py` enforces that in
    the `changes` job. So when `#11689` reworded the E2b floor note, the old pin
    demanded an in-place edit of a frozen evidence artefact and the ordering gate
    refused the PR — a pin whose only remedy is forbidden is not a pin.

    What is asserted instead is what actually holds and is load-bearing:

    * this log IS in the pins' `pre_existing_unscored_logs` exemption. That is the
      *reason* no reproduction pin applies, so if the exemption is ever withdrawn (the
      log would then have to be ordered normally, which no re-take can achieve) this
      reds instead of leaving a stale "does not apply" claim behind;
    * its bytes are a **constant digest**. Freezing removes the reproduction pin, not the
      anti-tamper one: a content pin stays satisfiable forever precisely because the log
      may never change again. Without it, an edit the census checker cannot see still
      changes the evidence — the checker reconciles the log with ITSELF, so it only catches
      an edit that makes two of the log's own lines disagree; editing a free-text/`note=`
      span (MEASURED: the checker still says "structurally sound") is invisible to it;
    * it is still the `UNPREREGISTERED`, exit-3 artefact the exemption is granted to,
      so it can never be read as a scored run.

    The current probe's printed text is verified against its OWN output by the note-
    state pins below, never against this historical artefact.

    ⛔ The ORDERING predicate — "was a commit that touches this log newer than the
    registration?" — has exactly ONE owner: the ordering gate in the `changes` job, which
    reads git history. This file keeps no second copy of that; a digest is a different
    fact (the bytes), not a re-derivation of the timeline.
    """
    pins = prereg_check.parse_pins(
        (_LOG.parent / "PRE-REGISTRATION.md").read_text(encoding="utf-8"))
    exempt = prereg_check._pins_mapping(pins, "pre_existing_unscored_logs")
    assert _LOG.name in exempt, (
        f"{_LOG.name} is no longer in the pins' pre_existing_unscored_logs "
        f"({sorted(exempt)}) — the reason this log is exempt from the probe-output "
        f"pin is gone, so either restore the exemption (PRE-REGISTRATION.md §0) or "
        f"order the log normally; a re-take cannot do the latter")

    committed = _LOG.read_text(encoding="utf-8")
    digest = hashlib.sha256(_LOG.read_bytes()).hexdigest()
    assert digest == _FROZEN_LOG_SHA256, (
        f"e1-e2.log's bytes changed (sha256 {digest}). This log is FROZEN by "
        f"PRE-REGISTRATION.md §0 — it is NOT re-taken, and a probe-message change does "
        f"not license an edit. Restore it with "
        f"`git checkout origin/main -- docs/evidence/7949-capstone/e1-e2.log` and put the "
        f"re-run in a NEW log. (If a change to this file is ever genuinely justified it is "
        f"an AMENDMENT to the pre-registration, argued in its own home — never a fresh "
        f"digest pasted here.)")
    assert "prereg=UNPREREGISTERED" in committed, (
        "e1-e2.log no longer declares itself UNPREREGISTERED — the exemption is "
        "granted only to a log that says so on its own line")
    findings, state = check.check(committed)
    assert findings == [], findings
    assert state["result"] == "UNPREREGISTERED", state["result"]


def test_checker_fails_when_a_denominator_is_missing(log_text):
    mutated = log_text.replace("numerator=15 denominator=25", "numerator=15 denominator=")
    findings, _ = check.check(mutated)
    assert any("[V3]" in f for f in findings), findings


def test_checker_fails_when_the_share_disagrees_with_its_counts(log_text):
    mutated = log_text.replace("share=0.600000", "share=0.900000", 1)
    findings, _ = check.check(mutated)
    assert any("is not 15/25" in f for f in findings), findings


def test_checker_fails_when_the_scored_arm_is_a_counterfactual(log_text):
    mutated = log_text.replace("scored_arm=after-vet-shipped",
                               "scored_arm=counterfactual-mint-skip")
    findings, _ = check.check(mutated)
    assert any("is a counterfactual" in f for f in findings), findings


def test_checker_fails_when_the_anti_gaming_control_is_absent(log_text):
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("e1-control "))
    findings, _ = check.check(mutated)
    assert any("[V5]" in f for f in findings), findings


def test_checker_fails_when_the_control_line_disagrees_with_its_arm(log_text):
    """The control line is reconciled with the arm table, not only with itself.

    #9110 round-6 review P2: both sides set to `1` keeps V5's fell-check green
    while the arms still print `count=90` — the anti-gaming control could then
    claim a population the run never had.
    """
    mutated = log_text.replace(
        "e1-control before_real_facts=90 after_real_facts=90",
        "e1-control before_real_facts=1 after_real_facts=1")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V5]" in f and "real_facts" in f for f in findings), findings


def test_checker_fails_when_the_e2_state_line_disagrees_with_its_arm(log_text):
    mutated = log_text.replace("e2-state before=42 after=42",
                               "e2-state before=7 after=7")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V6]" in f and "state_value_points" in f for f in findings), findings


def test_checker_fails_when_the_e2_atomic_line_disagrees_with_its_arm(log_text):
    mutated = log_text.replace("e2-atomic before=114 after=114",
                               "e2-atomic before=1 after=1")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V7]" in f and "atomic_points" in f for f in findings), findings


def test_checker_fails_when_a_state_value_table_shrinks(log_text):
    """#9110 round-6 review P1: `e2-state`/`e2-atomic` failed OPEN.

    A lost state-value table is the exact property E2 exists to protect. Before
    the sibling fell-check existed, `e2-state before=42 after=10` passed with
    zero findings — V6 only asserted the keys were digits.
    """
    mutated = log_text.replace("e2-state before=42 after=42",
                               "e2-state before=42 after=10")
    findings, _ = check.check(mutated)
    assert any("[V6]" in f and "FELL" in f for f in findings), findings


def test_checker_fails_when_an_atomicity_table_shrinks(log_text):
    mutated = log_text.replace("e2-atomic before=114 after=114",
                               "e2-atomic before=114 after=1")
    findings, _ = check.check(mutated)
    assert any("[V7]" in f and "FELL" in f for f in findings), findings


def _scored(text: str) -> str:
    """Turn the measured log into a SCORED, all-green one at a zero threshold.

    Used to exercise the fail-open shapes on a log the other rules ACCEPT: a
    green scored log is what `--require-green` passes, so it is the one that must
    not be allowed to hide a loss.
    """
    import re

    out = text
    for old, new in (("prereg=UNPREREGISTERED", "prereg=deadbeef"),
                     ("prereg_e1_threshold=n/a", "prereg_e1_threshold=0.0"),
                     ("threshold=n/a", "threshold=0.0"),
                     ("verdict=UNPREREGISTERED", "verdict=PASS"),
                     ("result=UNPREREGISTERED", "result=GREEN"),
                     ("exit=3", "exit=0")):
        assert old in out, old
        out = out.replace(old, new)
    # A SCORED log must carry the bar's binding (#9076): the corpus it measured
    # is the corpus line's own sha, so a synthetic scored log has to say so or it
    # is exactly the fail-open V15 refuses.
    sha = re.search(r"corpus_sha256=([0-9a-f]{64})", text).group(1)
    lines = out.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("prereg="))
    lines[at + 1:at + 1] = [
        "prereg_declared=deadbeef",
        f"prereg-binding status=MATCHED corpus=e1e2_corpus.json "
        f"corpus_sha256={sha} declared_path=docs/evidence/7949-capstone/corpus/"
        f"e1e2_corpus.json declared_sha256=n/a declared_floor=20 objects=25 "
        # The floor accounting a SCORED log must carry since #11689: which floors
        # this probe owns (and therefore checks) and which rows it skipped, each
        # with its owning criterion. A fixture that omitted them would be the
        # pre-fix shape the gate now refuses, so every fail-open pin built on this
        # scored log would be testing a log the checker rejects.
        f"floor_unconsumed=n/a "
        f"floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units "
        # A MATCHED binding owns all three floors, so all three DECLARED values are
        # numbers — `n/a` here is the unreadable shape, which V15 now refuses when
        # the same key is claimed as owned (#11689 review round 4).
        f"floor_skipped=n/a declared_before_floor=0.2 before_share=0.600000 "
        f"declared_surviving_floor=30 surviving_units=90 "
        f"surviving_units_basis=real_facts reason=n/a",
    ]
    return "\n".join(lines) + "\n"


def test_a_green_scored_log_cannot_hide_a_lost_state_value_table(log_text):
    """The end-to-end fail-open the review measured: `after=10` still GREEN.

    V6/V9/V10 all accept it today; the criterion line's own number is 10 while
    the after arm prints 42, so the reconciliation and the fell-check must fire.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    mutated = scored.replace("e2-state before=42 after=42",
                             "e2-state before=42 after=10")
    findings, _ = check.check(mutated)
    assert any("[V6]" in f and "FELL" in f for f in findings), findings


def test_a_green_scored_log_cannot_hide_a_lost_atomicity_table(log_text):
    scored = _scored(log_text)
    mutated = scored.replace("e2-atomic before=114 after=114",
                             "e2-atomic before=114 after=1")
    findings, _ = check.check(mutated)
    assert any("[V7]" in f and "FELL" in f for f in findings), findings


def test_checker_fails_when_a_criterion_verdict_contradicts_its_counts(log_text):
    """A `PASS` beside a fall, and a `FAIL` beside no fall, are both findings."""
    scored = _scored(log_text)
    lied = scored.replace("e2-state before=42 after=42",
                          "e2-state before=42 after=10")
    findings, _ = check.check(lied)
    assert any("is PASS while its own counts fell" in f for f in findings), findings
    failed_without_loss = scored.replace(
        "e2-atomic before=114 after=114 fused_after=1 verdict=PASS",
        "e2-atomic before=114 after=114 fused_after=1 verdict=FAIL")
    assert failed_without_loss != scored
    findings, _ = check.check(failed_without_loss)
    assert any("is FAIL while its own counts did not fall" in f
               for f in findings), findings


def test_checker_accepts_a_coverage_table_with_every_class_detected(log_text):
    """#9110 review P2: an EMPTY undetected list is a PASS, an ABSENT field is not.

    Every class having a detector is the outcome #4894 is meant to produce; a
    gate that rejects it has hard-coded today's coverage deficit into the bar. The
    scenario is the WHOLE disclosure — an empty `undetected_classes=` beside a
    table whose every row still shows no detector is a header contradicting its own
    table, which the next test pins as a finding.
    """
    header = next(ln for ln in log_text.splitlines()
                  if ln.startswith("coverage rows="))
    detected = re.sub(r"undetected_classes=.*$", "undetected_classes=", header)
    assert detected != header
    every_class_detected = log_text.replace(header, detected)
    for ln in log_text.splitlines():
        if ln.startswith("coverage class=") and "dd_positive=0 value_gate=0" in ln:
            every_class_detected = every_class_detected.replace(
                ln, ln.replace("value_gate=0", "value_gate=1"))
    findings, state = check.check(every_class_detected)
    assert findings == [], findings
    assert state["undetected_classes"] == []
    absent = re.sub(r" undetected_classes=\S*", "", header)
    assert absent != header
    findings, _ = check.check(log_text.replace(header, absent))
    assert any("[V8]" in f and "undetected_classes" in f for f in findings), findings


def test_checker_fails_when_the_coverage_header_contradicts_its_table(log_text):
    """#9110 review P2 (M2): the two coverage disclosures must be each other.

    Deleting the per-class table used to pass — the `per_class and …` guard made an
    absent table behave like an all-detected one. Requiring the rows closes that,
    and this pins the other direction: a header whose `undetected_classes=` list
    does not match what the rows show is a finding.
    """
    header = next(ln for ln in log_text.splitlines()
                  if ln.startswith("coverage rows="))
    lied = re.sub(r"undetected_classes=.*$", "undetected_classes=branch_name", header)
    assert lied != header
    findings, _ = check.check(log_text.replace(header, lied))
    assert any("[V8]" in f and "disagree" in f for f in findings), findings
    # ... and the per-class column must sum to the header's positively_classified
    row = next(ln for ln in log_text.splitlines()
               if ln.startswith("coverage class=definite_description "))
    findings, _ = check.check(
        log_text.replace(row, row.replace("dd_positive=9", "dd_positive=7")))
    assert any("[V8]" in f and "dd_positive" in f for f in findings), findings


def test_checker_fails_when_an_unpreregistered_log_carries_a_scored_verdict(
        log_text):
    """The reverse V10 direction: no frozen bar means no scored verdict at all."""
    mutated = log_text.replace(
        "e2-atomic before=114 after=114 fused_after=1 verdict=UNPREREGISTERED",
        "e2-atomic before=114 after=114 fused_after=1 verdict=FAIL")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V10]" in f and "UNPREREGISTERED" in f for f in findings), findings


def test_checker_fails_when_a_red_log_has_no_failed_criterion(log_text):
    """RED means 'scored and something red' — with nothing scored it is a lie."""
    mutated = log_text.replace("result=UNPREREGISTERED", "result=RED")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V10]" in f and "no criterion is FAIL" in f for f in findings), findings


def test_checker_fails_when_a_criterion_passes_without_a_frozen_threshold(log_text):
    mutated = log_text.replace("e1-control before_real_facts=90 after_real_facts=90 "
                               "verdict=UNPREREGISTERED",
                               "e1-control before_real_facts=90 after_real_facts=90 "
                               "verdict=PASS")
    findings, _ = check.check(mutated)
    assert any("no frozen threshold" in f for f in findings), findings


def test_checker_fails_when_coverage_honesty_is_missing(log_text):
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("coverage "))
    findings, _ = check.check(mutated)
    assert any("[V8]" in f for f in findings), findings


def test_checker_fails_when_split_is_not_declared_unimplemented(log_text):
    mutated = log_text.replace("split-implemented=no", "split-implemented=yes")
    findings, _ = check.check(mutated)
    assert any("[V7]" in f for f in findings), findings


def test_checker_fails_when_a_dropped_point_names_no_cause(log_text):
    line = ("arm=after-vet-shipped dropped kind=point content='the thing' "
            "cause= values=['4.2']")
    findings, _ = check.check(log_text + line + "\n")
    assert any("[V6]" in f for f in findings), findings


def test_checker_fails_when_the_e1_reduction_is_borrowed_from_a_counterfactual(
        log_text):
    """The `e1` line is the one the VERDICT is read from — reconcile it (#9110 P1).

    The exact mutation the review measured as passing: keep both shares at 0.6 (as
    every arm line prints) and claim the counterfactual's 22.5 pp as the SHIPPED
    arm's reduction — the number this whole bundle exists to attribute to a
    mechanism that does not exist in production.
    """
    assert "reduction=-0.000000" in log_text
    mutated = log_text.replace("reduction=-0.000000", "reduction=0.225000")
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "difference" in f for f in findings), findings


def test_checker_fails_when_the_e1_shares_disagree_with_the_arm_lines(log_text):
    mutated = log_text.replace(
        "before_share=0.600000 after_share=0.600000",
        "before_share=0.900000 after_share=0.900000")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "before_share" in f for f in findings), findings
    assert any("[V3]" in f and "after_share" in f for f in findings), findings


def test_checker_fails_when_the_scored_arm_counts_were_edited_consistently(log_text):
    """A self-consistent edit to the after arm still has to match the e1 line."""
    original = ("arm=after-vet-shipped metric=objects total=25 numerator=15 "
                "denominator=25 share=0.600000")
    assert original in log_text
    mutated = log_text.replace(
        original,
        "arm=after-vet-shipped metric=objects total=20 numerator=1 "
        "denominator=20 share=0.050000")
    findings, _ = check.check(mutated)
    # V3 is satisfied by the arms (1/20 is 0.05) — the e1 line is what objects
    assert any("[V3]" in f and "after_share" in f for f in findings), findings


def test_checker_fails_when_the_observation_contradicts_the_reduction(log_text):
    mutated = log_text.replace("observed=NO_REDUCTION", "observed=REDUCTION_OBSERVED")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V10]" in f and "contradicts" in f for f in findings), findings
    # ... and the reverse direction, so neither side can drift alone
    back = log_text.replace("reduction=-0.000000", "reduction=0.225000")
    back_findings, _ = check.check(back)
    assert any("[V10]" in f and "NO_REDUCTION" in f for f in back_findings), back_findings


def test_checker_requires_green_when_asked(log_text, tmp_path):
    # the log's result is UNPREREGISTERED, so --require-green must exit 1
    path = tmp_path / "e1-e2.log"
    path.write_text(log_text, encoding="utf-8")
    assert check.main([str(path)]) == 0
    assert check.main([str(path), "--require-green"]) == 1


# ── 4b. the gate cannot pass by OMISSION (#9110 review P2) ────────────────
#
# Every rule above reconciles the lines it is GIVEN, so all three of these
# deletions used to pass with ZERO findings and exit 0 — the log could drop the
# counterfactual set (the 22.5 pp estimate and the arbiter arm that carries this
# bundle's central finding), the whole per-class coverage table, or every
# value-literal line, and still be called structurally sound. "Absent" is not
# "empty" and it is not a pass: the same treatment V7 gives
# `split-implemented=no` now applies to the rest of the disclosure set.


def test_checker_fails_when_every_counterfactual_arm_is_deleted(log_text):
    """M1: both counterfactual arms, the mint-skip transform and its estimates."""
    mutated = "\n".join(
        ln for ln in log_text.splitlines()
        if not (ln.startswith("arm=after-vet-arbiter ")
                or ln.startswith("arm=counterfactual-mint-skip ")
                or ln.startswith("mint_skip ")
                or ln.startswith("counterfactual arm=")))
    assert "arm=after-vet-arbiter counterfactual=" not in mutated
    assert "mint_skip " not in mutated
    findings, _ = check.check(mutated)
    assert any("[V2]" in f and "after-vet-arbiter" in f for f in findings), findings
    assert any("[V2]" in f and "counterfactual-mint-skip" in f
               for f in findings), findings
    assert any("[V2]" in f and "no counterfactual arm" in f for f in findings), findings
    assert any("[V4]" in f and "mint-skip" in f for f in findings), findings


def test_checker_fails_when_the_counterfactual_estimates_are_deleted(log_text):
    """The `counterfactual arm=` lines are the only place the estimates appear."""
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("counterfactual arm="))
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V4]" in f and "states no `counterfactual arm=`" in f
               for f in findings), findings


def test_checker_fails_when_a_counterfactual_estimate_disagrees_with_its_arm(
        log_text):
    """The 22.5 pp estimate is reconciled with the arm that produced it."""
    original = ("counterfactual arm=counterfactual-mint-skip reduction=0.225000 "
                "real_facts=90 state_values=42")
    assert original in log_text
    mutated = log_text.replace(
        original,
        "counterfactual arm=counterfactual-mint-skip reduction=0.300000 "
        "real_facts=90 state_values=42")
    findings, _ = check.check(mutated)
    assert any("[V4]" in f and "difference" in f for f in findings), findings
    lied = log_text.replace(
        original,
        "counterfactual arm=counterfactual-mint-skip reduction=0.225000 "
        "real_facts=89 state_values=42")
    findings, _ = check.check(lied)
    assert any("[V4]" in f and "real_facts" in f for f in findings), findings


def test_checker_fails_when_the_per_class_coverage_table_is_deleted(log_text):
    """M2: the 2-detector table is the disclosure V8 exists to protect."""
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("coverage class="))
    assert "coverage class=" not in mutated
    assert "coverage rows=" in mutated
    findings, _ = check.check(mutated)
    assert any("[V8]" in f and "no per-class rows" in f for f in findings), findings


def test_checker_fails_when_the_value_literal_lines_are_deleted(log_text):
    """M3: `values=42` is unauditable without the matches it counts."""
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if " metric=state_value_literals " not in ln)
    assert "metric=state_value_literals" not in mutated
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "unauditable" in f for f in findings), findings


def test_checker_fails_when_the_value_count_is_not_the_printed_set(log_text):
    """The one number E2 is read from must equal the literals printed beside it."""
    mutated = log_text.replace(
        "arm=before metric=state_value_points points=34 values=42",
        "arm=before metric=state_value_points points=34 values=7")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V3]" in f and "not the set it counts" in f for f in findings), findings


def test_checker_fails_when_the_discriminating_control_is_deleted(log_text):
    """#9110 review P2: without the control the 0.0 pp has two explanations."""
    mutated = "\n".join(ln for ln in log_text.splitlines()
                        if not ln.startswith("control variant="))
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V12]" in f and "discriminating control" in f
               for f in findings), findings


def test_checker_fails_when_the_control_did_not_remove_what_it_stripped(log_text):
    """The control's claim is *the discards LAND*; a partial landing is not it."""
    line = next(ln for ln in log_text.splitlines()
                if ln.startswith("control variant="))
    mutated = log_text.replace(line, line.replace("removed=9", "removed=0"))
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V12]" in f and "removes 0 Object" in f for f in findings), findings
    stripped_nothing = log_text.replace(line, line.replace(
        "stripped_objects=9", "stripped_objects=0"))
    findings, _ = check.check(stripped_nothing)
    assert any("[V12]" in f for f in findings), findings


def test_checker_fails_when_the_control_names_nothing_it_removed(log_text):
    """The count was checkable; the identities behind it were not (#9110 P3)."""
    line = next(ln for ln in log_text.splitlines()
                if ln.startswith("control variant="))
    assert "removed_names=" in line
    mutated = log_text.replace(line, re.sub(r"removed_names=\[.*?\] ", "", line))
    assert "removed_names=" not in mutated
    findings, _ = check.check(mutated)
    assert any("[V12]" in f and "name what it removed" in f
               for f in findings), findings
    # ... and the names are the set the count counts: `removed=9` beside 8 names
    # is the same defect one level up from the deleted field.
    names = json.loads(re.search(r"removed_names=(\[.*?\]) ", line).group(1))
    short = log_text.replace(
        line, line.replace(json.dumps(names, ensure_ascii=False), "[]"))
    findings, _ = check.check(short)
    assert any("[V12]" in f and "names 0" in f for f in findings), findings


# ── 4c. the MECHANISM block: the gate that is the finding must have RUN ───
#
# The bundle's central finding is that the shipped gate fired and ``apply_vet``
# Rule 2 downgraded every discard, so E1 could not move. That mechanism is
# disclosed only by the ``metric=vet`` / ``metric=vet-downgrades`` /
# ``vet-warning=`` lines — and until now NO rule read any of them, so the scored
# arm could print ``enabled=no``, ``discarded=0`` and no warnings at all and still
# be called structurally sound. A gate that never ran produces the same 0.0 pp as
# one that ran and could not move, so every other number in the log stayed put
# (#9110 review P2, measured as five accepted mutations).


def test_checker_fails_when_the_scored_arm_ran_with_the_gate_off(log_text):
    """`enabled=no` is legal ONLY on the baseline arm."""
    mutated = log_text.replace(
        "arm=after-vet-shipped metric=vet pass=s2 enabled=yes",
        "arm=after-vet-shipped metric=vet pass=s2 enabled=no")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V13]" in f and "enabled=no" in f for f in findings), findings
    # the baseline arm is where `no` belongs — and it must stay accepted
    baseline = "arm=before metric=vet pass=s2 enabled=no"
    assert baseline in log_text
    assert not [f for f in check.check(log_text)[0] if "[V13]" in f]


def test_checker_fails_when_the_candidate_count_is_not_its_outcome_table(
        log_text):
    """`candidates=` and `discarded=` are reconciled with `by_outcome=`."""
    original = ("candidates=140 discarded=3 "
                'by_outcome={"DISCARD": 3, "KEEP": 137}')
    assert original in log_text
    more = log_text.replace(original, original.replace("candidates=140",
                                                       "candidates=999"), 1)
    findings, _ = check.check(more)
    assert any("[V13]" in f and "sum of by_outcome" in f
               for f in findings), findings
    # A discard count of 0 beside a DISCARD outcome is the exact shape that makes
    # "the gate ran and removed 3" unreadable.
    zeroed = log_text.replace(
        original,
        'candidates=140 discarded=0 by_outcome={"DISCARD": 3, "KEEP": 137}', 1)
    findings, _ = check.check(zeroed)
    assert any("[V13]" in f and "DISCARD outcome count" in f
               for f in findings), findings


def test_checker_fails_when_the_downgrade_count_is_not_its_warnings(log_text):
    """`vet-downgrades count=6` must be the 6 warnings printed beside it."""
    mutated = log_text.replace(
        "arm=after-vet-shipped metric=vet-downgrades count=6",
        "arm=after-vet-shipped metric=vet-downgrades count=0")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V14]" in f and "not the set of downgrades" in f
               for f in findings), findings


def test_checker_fails_when_an_arms_downgrade_warnings_are_deleted(log_text):
    """Deleting the warnings while the count stands is the same disagreement."""
    mutated = "\n".join(
        ln for ln in log_text.splitlines()
        if not (ln.startswith("arm=after-vet-shipped vet-warning=")
                and "kept (Layer-1 referential integrity)" in ln))
    assert mutated != log_text
    assert "arm=after-vet-shipped metric=vet-downgrades count=6" in mutated
    findings, _ = check.check(mutated)
    assert any("[V14]" in f and "count=6" in f for f in findings), findings


def test_checker_fails_when_the_mechanism_block_is_deleted(log_text):
    """An absent mechanism block is not "the gate did nothing"."""
    mutated = "\n".join(
        ln for ln in log_text.splitlines()
        if not (" metric=vet " in ln or " metric=vet-downgrades " in ln))
    assert "metric=vet" not in mutated
    findings, _ = check.check(mutated)
    assert any("[V13]" in f and "no metric=vet line" in f
               for f in findings), findings
    assert any("[V14]" in f and "no metric=vet-downgrades line" in f
               for f in findings), findings


def test_checker_fails_when_the_fused_count_is_not_the_fused_lines(log_text):
    """`fused_after` counts the fused rows printed under it (#9110 P3)."""
    mutated = log_text.replace("fused_after=1 verdict=", "fused_after=0 verdict=")
    assert mutated != log_text
    findings, _ = check.check(mutated)
    assert any("[V7]" in f and "set it counts" in f for f in findings), findings
    assert any("[V7]" in f and "disagrees with its own arm" in f
               for f in findings), findings


def test_checker_fails_when_the_state_value_point_counts_are_missing(log_text):
    """"42 over 34 Points" loses its denominator silently (#9110 P3)."""
    for key in ("before_points", "after_points"):
        mutated = re.sub(rf" {key}=\d+", "", log_text)
        assert mutated != log_text
        findings, _ = check.check(mutated)
        assert any("[V6]" in f and key in f for f in findings), (key, findings)
    lied = log_text.replace("before_points=34", "before_points=99")
    findings, _ = check.check(lied)
    assert any("[V6]" in f and "before_points=99" in f
               for f in findings), findings


def test_checker_fails_when_the_mint_skip_disclosures_are_missing(log_text):
    """The mint-skip claim is about REFERENCES, not only removed Objects."""
    for key in ("stripped_references", "statements_left_unreferenced"):
        mutated = re.sub(rf" {key}=\d+", "", log_text)
        assert mutated != log_text
        findings, _ = check.check(mutated)
        assert any("[V4]" in f and key in f for f in findings), (key, findings)
    impossible = log_text.replace("statements_left_unreferenced=9",
                                 "statements_left_unreferenced=11")
    findings, _ = check.check(impossible)
    assert any("[V4]" in f and "unreferenced" in f for f in findings), findings


# ── 5. the pre-registration binding ───────────────────────────────────────


@pytest.mark.parametrize("artifact", [
    # the shape #9075 has not landed yet: an artifact with no E1 bar
    {"e2": {"min_state_value_recall": 1.0}},
    # an `e1` that is not a mapping — the pre-fix shape crashed with AttributeError
    {"e1": "0.05"},
    # a bar that is not a number — the pre-fix shape crashed inside float()
    {"e1": {"min_share_reduction": "5%"}},
    # a bool is an int in Python; a bar of `true` is not a bar
    {"e1": {"min_share_reduction": True}},
])
def test_a_malformed_preregistration_measures_and_never_scores(artifact, monkeypatch):
    """#9110 round-6 review P2: the artifact is validated, not consumed blind.

    The pre-fix probe keyed `scored` on `prereg is not None`, so a dict without
    `e1.min_share_reduction` printed four `UNPREREGISTERED` criteria and then
    `result=RED` / `exit=1` — telling an operator a bar had been missed when none
    existed. A non-mapping `e1`, or a non-numeric bar, raised out of `float()` and
    the run wrote no log at all. Both must MEASURE (exit 3) and say why.
    """
    import io
    from contextlib import redirect_stdout

    monkeypatch.setattr(probe, "load_prereg", lambda sha: artifact)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 3, text
    assert "prereg-error=" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "result=RED" not in text, text
    assert "verdict=UNPREREGISTERED" in text, text
    findings, state = check.check(text)
    assert findings == [], findings
    assert state["result"] == "UNPREREGISTERED"


def test_a_valid_preregistration_is_read_once_into_a_numeric_bar(monkeypatch):
    """The accepted shape still scores — validation must not refuse a good bar.

    "Good" now includes the corpus pin (#9076): a threshold alone is not a bar
    for THIS population, which is what `_bound_artifact` supplies here.
    """
    import io
    from contextlib import redirect_stdout

    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: {**_bound_artifact(0.05),
                                     "e2": {"min_recall": 1.0}})
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 1, text
    assert "result=RED" in text
    assert "prereg-error=" not in text
    assert "prereg_e1_threshold=0.05" in text


def test_scoring_uses_the_frozen_threshold_and_can_be_red(monkeypatch):
    """With a threshold the measured 0.0 reduction cannot meet, the run is RED."""
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: _bound_artifact(0.05))
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 1, text
    assert "result=RED" in text
    assert "e1 before_share=0.600000 after_share=0.600000" in text
    assert "threshold=0.05" in text
    assert "verdict=FAIL" in text


def test_a_zero_threshold_greens_the_arm_but_the_observation_still_says_no(
        monkeypatch):
    """A zero threshold is MET by a 0.0 reduction — and cannot hide it.

    This is the threshold-meets-the-run leg, NOT the control-conjunction leg
    (that one is ``test_a_red_control_keeps_a_scoring_run_red`` below): every
    control holds in the produced log, so what is pinned here is that a bar the
    run meets greens it while the threshold-free ``observed=`` line still reports
    that the share did NOT move.
    """
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: _bound_artifact(0.0))
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert "threshold=0.0" in text
    assert code == 0, text
    assert "result=GREEN" in text
    assert "observed=NO_REDUCTION" in text, (
        "a zero threshold must not hide that the share did not move")


def test_a_red_control_keeps_a_scoring_run_red(monkeypatch):
    """A threshold the run DOES meet cannot go green while a control is red.

    Pins the control conjunction in the probe's
    ``green = scored and e1_ok and control_ok and state_ok and atomic_ok``
    (#9110 review P2). Without this pin the terms could be deleted and every
    other test in this file would still pass — leaving a run that LOST a planted
    must-keep fact printing ``result=GREEN`` and exiting 0, which is exactly what
    ``--require-green`` accepts. The loss is injected at the ``run_arm`` seam: the
    SHIPPED arm's payload drops one planted unit, which is what a gate that bought
    E1 with facts would produce. E1 itself still meets its 0.0 bar, so the only
    red criterion is the control.
    """
    import io
    from contextlib import redirect_stdout

    corpus = probe.load_corpus(_CORPUS)
    keep = {p["content"] for p in corpus["points"] if p.get("must_keep")}
    real_run_arm = probe.run_arm

    def run_arm_dropping_one_planted_fact(embed_list, *, vet, arbiter=None):
        res = real_run_arm(embed_list, vet=vet, arbiter=arbiter)
        shipped = (vet and arbiter is None
                   and len(embed_list["entities"]) == len(corpus["objects"]))
        if not shipped:
            return res
        points = list(res["payload"].get("points") or [])
        idx = next(i for i, p in enumerate(points) if p.get("content") in keep)
        payload = dict(res["payload"])
        payload["points"] = points[:idx] + points[idx + 1:]
        return {**res, "payload": payload}

    monkeypatch.setattr(probe, "run_arm", run_arm_dropping_one_planted_fact)
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: _bound_artifact(0.0))
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    control = next(ln for ln in text.splitlines() if ln.startswith("e1-control "))
    e1 = next(ln for ln in text.splitlines() if ln.startswith("e1 "))
    assert "after_real_facts=89" in control, control
    assert "verdict=FAIL" in control, control
    # the E1 bar itself is met — so the RED below is the control's doing, not E1's
    assert "after_share=0.600000" in e1 and "verdict=PASS" in e1, e1
    assert code == 1, text
    assert "result=RED" in text
    assert "result=GREEN" not in text


def test_the_threshold_used_must_be_the_one_the_artifact_froze(monkeypatch):
    """A log that names a pre-registration and scores against its OWN bar fails."""
    monkeypatch.setattr(probe, "load_prereg",
                        lambda sha: _bound_artifact(0.05))
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    # the pair is printed: what the artifact froze, and what the run used
    assert "prereg_e1_threshold=0.05" in text
    assert "threshold=0.05" in text
    findings, _ = check.check(text)
    assert findings == [], findings
    # mutate the USED bar only — the run now scores against a threshold the
    # artifact never stated, and the checker must refuse it. The leading space is
    # load-bearing: without it the FIRST match is `prereg_e1_threshold=0.05`, the
    # FROZEN side, which is the wrong direction to tamper and passes anyway
    # because V9's comparison is symmetric (#9110 review P2).
    tampered = text.replace(" threshold=0.05", " threshold=0.01", 1)
    assert "prereg_e1_threshold=0.05" in tampered, (
        "the FROZEN threshold must be untouched — only the used bar is tampered")
    assert " threshold=0.01" in tampered
    findings, _ = check.check(tampered)
    assert any("the bar moved" in f for f in findings), findings


def test_a_prereg_commit_without_the_artifact_stays_unpreregistered(monkeypatch):
    """`git show` reads the COMMIT's tree — a missing artifact cannot be scored.

    The absence is injected at the ``git show`` seam rather than pinned on a named
    commit (#9110 review P2). A HEAD-based pin — the original shape — self-invalidates
    the moment #9075 lands ``preregistration.json``, which is the very artifact this
    probe says it is waiting for: `load_prereg` would then succeed and this file
    would break in whichever lane lands #9075. A fixed pre-artifact sha has the
    mirror-image flaw — if a squash-merge rewrites it the case silently becomes
    "the sha does not exist", which is not the property under test. The seam keeps
    the property itself: the artifact is ABSENT from the tree `git show` is asked
    for, and the run must MEASURE rather than invent a verdict.
    """
    import io
    import subprocess
    from contextlib import redirect_stdout

    real_git = probe._git

    def git_whose_tree_lacks_the_artifact(*args: str) -> str:
        if args and args[0] == "show":
            raise subprocess.CalledProcessError(
                128, ["git", *args],
                output="",
                stderr=f"fatal: path '{probe.PREREG_ARTIFACT}' does not exist "
                       f"in 'deadbeef'")
        return real_git(*args)

    monkeypatch.setattr(probe, "_git", git_whose_tree_lacks_the_artifact)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 3, text
    assert "prereg-error=" in text
    assert "result=UNPREREGISTERED" in text


# ── 5b. the bar must be bound to the population it was argued for ─────────


def _run_with_prereg(monkeypatch, artifact):
    """Run the probe against a synthetic artifact through the `load_prereg` seam."""
    import io
    from contextlib import redirect_stdout

    monkeypatch.setattr(probe, "load_prereg", lambda sha: artifact)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    return code, buf.getvalue()


def test_a_preregistration_for_another_corpus_never_scores(monkeypatch):
    """#9076: a parseable bar pinned to a DIFFERENT corpus must not score.

    The pre-fix probe consumed `e1.min_share_reduction` and never read
    `corpus.path`, so this artifact — a bar argued over `tests/eval/write_path` —
    scored the 25-Object `e1e2_corpus.json` run and printed `prereg=<sha>` with
    `result=RED`. That is a verdict on a number whose denominator the bar never
    saw: the numerator/denominator mismatch the issue refuses one level up.
    Measured pre-fix exit 1, post-fix exit 3, and the log names which corpus.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "corpus": {"path": "tests/eval/write_path"}})
    assert code == 3, text
    assert "prereg-binding status=MISMATCH" in text, text
    assert "reason=corpus-path" in text, text
    assert "declared_path=tests/eval/write_path" in text, text
    assert "prereg=UNPREREGISTERED" in text, text
    assert "verdict=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, state = check.check(text)
    assert findings == [], findings
    assert state["prereg_binding"]["status"] == "MISMATCH"


def test_a_preregistration_with_no_corpus_pin_never_scores(monkeypatch):
    """A threshold with no corpus cannot say which population its bar covers.

    The pre-fix shape is exactly this dict, and it scored: `_bound_artifact`'s
    pins are what make a bar scoreable at all. Absent is not a pass.
    """
    code, text = _run_with_prereg(
        monkeypatch, {"e1": {"min_share_reduction": 0.05}})
    assert code == 3, text
    assert "prereg-binding status=UNPINNED" in text, text
    assert "reason=corpus-unpinned" in text, text
    assert "verdict=UNPREREGISTERED" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_denominator_below_the_frozen_floor_is_refused_not_failed(monkeypatch):
    """`below_floor: REFUSED, which is neither a pass nor a fail` — measured.

    #9507's record freezes a 30-Object floor for E1 and this corpus has 25, so a
    re-run of E1/E2 over it can be reported neither RED (a missed bar) nor GREEN.
    The pre-fix probe had no floor at all and would have said one or the other.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": 30}})
    assert code == 3, text
    assert "prereg-binding status=BELOW_FLOOR" in text, text
    assert "declared_floor=30 objects=25" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floor", ["30", 30.0, True, [30]])
def test_a_declared_but_unreadable_floor_is_refused_not_read_as_absent(
        monkeypatch, floor):
    """A floor the probe cannot read is a floor it cannot honour (#9513 review P2).

    `_declared_floor` used to return `None` for ANY non-int value, so an artifact
    that declared the floor as `"30"` (a JSON typo), `30.0`, or `true` lost the
    gate silently: the binding came back MATCHED, the threshold parsed, and the
    25-Object run scored RED/GREEN against a bar whose author had refused it —
    the same mismatch #9076 exists to refuse, through the one field read
    permissively. "Absent" and "present but unreadable" are different states.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": floor}})
    assert code == 3, text
    assert "prereg-binding status=UNREADABLE" in text, text
    assert "reason=floor-unreadable" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_declared_floor_that_is_met_still_scores(monkeypatch):
    """The refusal is about non-INTEGER floors, not about declaring one."""
    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "status=MATCHED" in binding and binding.endswith("reason=n/a"), binding
    assert "declared_floor=20 objects=25" in binding, binding


def test_checker_refuses_a_matched_binding_carrying_a_reason(monkeypatch):
    """A MATCHED binding with a refusal reason beside it is a contradiction."""
    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    tampered = text.replace(
        "reason=n/a",
        "reason=floor-unreadable", 1)
    assert "reason=floor-unreadable" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "nothing to refuse" in f for f in findings), findings


def test_checker_refuses_an_unreadable_binding_with_no_attribution(monkeypatch):
    """`UNREADABLE` must say which declared value it could not read."""
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": "30"}})
    assert code == 3, text
    tampered = text.replace("reason=floor-unreadable", "reason=n/a", 1)
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "could not be read" in f for f in findings), findings


@pytest.mark.parametrize("artifact", [None, [], "", 0, False])
def test_a_non_object_artifact_is_reported_as_malformed(monkeypatch, artifact):
    """A committed `null` must not read as "no --prereg was given".

    This pin holds the MERGE-BASE behaviour, not a pre-fix hole: at `8afd83a53`
    the validation call (`prereg_threshold`) sat INSIDE the `try` that reports
    `prereg-error=`, so `json.loads("null")` -> `None` already produced
    `prereg-error=TypeError: ... not an object`, `result=UNPREREGISTERED`, exit 3
    — so this case does NOT discriminate against the merge-base. The refactor in
    this PR moved `load_prereg` out of that `try`, which would have let a
    non-object artifact skip the binding, the threshold AND the `prereg-error=`
    line, leaving an operator who passed `--prereg <sha>` with a bare
    UNPREREGISTERED indistinguishable from passing nothing. The pin keeps the
    refactor honest: the module docstring promises the unconsumable case is
    reported, and every non-object shape must keep saying so.
    """
    code, text = _run_with_prereg(monkeypatch, artifact)
    assert code == 3, text
    assert "prereg-error=TypeError:" in text, text
    assert "not an object" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "prereg-binding" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_corpus_pin_with_an_embedded_nul_is_refused_not_crashed(monkeypatch):
    """A malformed pin must refuse (exit 3), never crash (#9513 review P3).

    Valid JSON can carry a NUL (`"path": "...\u0000x"`), and `Path.resolve()`
    raises ValueError — not OSError — on it, so the pre-fix arm let the exception
    escape: the probe died with a traceback and exit 1, the code a runner reads as
    RED, while the docstring promises the unconsumable case on `prereg-error=`.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "corpus": {"path": _CORPUS_REL + "\u0000x"}})
    assert code == 3, text
    assert "prereg-binding status=MISMATCH" in text, text
    assert "reason=corpus-path" in text, text
    assert "result=UNPREREGISTERED" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_matched_corpus_with_an_unreadable_bar_is_not_named_as_preregistered(
        monkeypatch):
    """A bound corpus does not make a malformed BAR a scored bar (#9513 review P2).

    `prereg=` used to key off the binding, so this artifact produced
    `prereg=<sha>` beside `prereg_e1_threshold=n/a` and `prereg-error=` — a log
    the checker's V9 calls unsound. The probe must not emit a log its own gate
    rejects, and `prereg=` must never claim a run scored against a bar it could
    not read.
    """
    code, text = _run_with_prereg(
        monkeypatch, {"e1": {"min_share_reduction": "5%"},
                      "corpus": {"path": _CORPUS_REL},
                      "denominator_floors": {"e1_objects": 20}})
    assert code == 3, text
    assert "prereg=UNPREREGISTERED" in text, text
    assert "prereg-binding status=MATCHED" in text, text
    assert "prereg-error=" in text, text
    assert "prereg_e1_threshold=n/a" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_the_unreadable_floor_refusal_names_the_value_it_could_not_read(
        monkeypatch):
    """`floor-unreadable` without the declared value is not auditable (P3)."""
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": "30"}})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert 'declared_floor="30"' in binding, binding
    assert "reason=floor-unreadable" in binding, binding


def test_checker_refuses_a_binding_denominator_the_run_did_not_produce(monkeypatch):
    """`objects=` must be the scored arm's own denominator (P2, tamper-facing)."""
    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    # dodge the floor by inflating the binding's denominator only: the arm still
    # prints denominator=25, so the two no longer agree.
    tampered = text.replace("declared_floor=20 objects=25",
                            "declared_floor=30 objects=30", 1)
    assert "declared_floor=30 objects=30" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "did not produce" in f for f in findings), findings


def test_checker_refuses_a_scored_log_whose_binding_is_not_matched(log_text):
    """The T2 boundary: a SCORED log may only carry a MATCHED binding.

    #11689 review round 7: this rule was UNPINNED — deleting it left 157 tests green
    while a log with `result=GREEN` and `status=BELOW_FLOOR reason=below-floor`
    certified with zero findings, i.e. a run under its own floor scoring. The four
    refusal-metadata rules beside it were unpinned too, so each gets its pin here.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    # (a) scored against a below-floor bar
    unbind = scored.replace("status=MATCHED", "status=BELOW_FLOOR", 1)
    unbind = unbind.replace("reason=n/a", "reason=below-floor", 1)
    findings, _ = check.check(unbind)
    assert any("[V15]" in f and "SCORED against a bar whose binding is" in f
               for f in findings), findings
    # (b) a population that is not a count
    frac = scored.replace("surviving_units=90", "surviving_units=90.0", 1)
    findings, _ = check.check(frac)
    assert any("[V15]" in f and "not a count" in f for f in findings), findings
    # (c) a MISMATCH with no recognised reason
    noname = scored.replace("status=MATCHED", "status=MISMATCH", 1)
    findings, _ = check.check(noname)
    assert any("[V15]" in f and "no recognised reason" in f
               for f in findings), findings
    # (d) a scored log whose binding states no denominator
    noden = scored.replace("declared_floor=20 objects=25", "declared_floor=20", 1)
    bind_line = next(ln for ln in noden.splitlines()
                     if ln.startswith("prereg-binding "))
    assert "objects=" not in bind_line, bind_line
    findings, _ = check.check(noden)
    assert any("[V15]" in f and "does not state the denominator" in f
               for f in findings), findings
    # (e) a MATCHED binding inside an UNPREREGISTERED log, with nothing else to say
    stuck = scored.replace("result=GREEN", "result=UNPREREGISTERED", 1)
    stuck = stuck.replace("prereg=deadbeef", "prereg=UNPREREGISTERED", 1)
    findings, _ = check.check(stuck)
    assert any("[V15]" in f and "no reason is stated for refusing to score" in f
               for f in findings), findings


def test_checker_refuses_a_skip_that_sheds_an_owned_floor(log_text):
    """`floor_skipped=` may not be used to delegate a floor THIS probe owns.

    #11689 review round 6 P1: the bucket exists for rows another instrument scores,
    and nothing stopped a log from moving an owned key there with a made-up owner —
    so a SCORED log could certify a run whose owned floors were never compared
    (measured: `floors_owned=n/a` + all three floors skipped → GREEN, zero findings).
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    shed = scored.replace(
        "floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units",
        "floors_owned=n/a", 1)
    shed = shed.replace(
        "floor_skipped=n/a",
        "floor_skipped=e1_objects:A1,e1_min_before_share:E1,"
        "e2b_surviving_units:E2b", 1)
    assert "floors_owned=n/a" in shed, shed
    findings, _ = check.check(shed)
    assert any("[V15]" in f and "may not be delegated" in f
               for f in findings), findings


def test_checker_refuses_a_binding_that_owns_a_floor_it_never_read(log_text):
    """`floors_owned=` is a CLAIM, and V15 now checks the mirror of it.

    #11689 review round 4: the gate verified "a numeric `declared_*` must be named
    on `floors_owned=`" but not the converse, so a log could claim ownership of a
    floor nothing compared — including a key this gate knows no pair for at all —
    and pass with zero findings. Both shapes below scored GREEN before the mirror.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    # (a) an owned key the gate knows no declared/measured pair for
    invented = scored.replace(
        "floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units",
        "floors_owned=a1_objects,e1_min_before_share,e1_objects,"
        "e2b_surviving_units", 1)
    findings, _ = check.check(invented)
    assert any("[V15]" in f and "knows no declared/measured pair" in f
               for f in findings), findings
    # (b) a known owned key whose declared side is `n/a` — "owned and never read"
    unread = scored.replace("declared_surviving_floor=30",
                            "declared_surviving_floor=n/a", 1)
    findings, _ = check.check(unread)
    assert any("[V15]" in f and "names a floor it never read" in f
               for f in findings), findings


def test_checker_refuses_a_matched_binding_that_names_no_pin(log_text):
    """A MATCHED bar that names NO pin is the UNPINNED case relabelled (P2).

    The probe's `prereg_binding` contract is `MATCHED` only when a pin exists,
    it agrees, and no floor is violated; the absence of a pin is `UNPINNED`.
    V15 used to read only `reason` and `objects`/`declared_floor` in its MATCHED
    branch, so `declared_path=n/a declared_sha256=n/a` — an unbound bar with the
    right status word — passed every rule: the status is in the tuple, the sha
    reconciles with `corpus=`, `reason=n/a` is what a MATCHED line says, and the
    denominator meets the floor. That is a relabel, not an absent line.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    tampered = scored.replace(f"declared_path={_CORPUS_REL}",
                              "declared_path=n/a")
    assert "declared_path=n/a declared_sha256=n/a" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "names no pin" in f for f in findings), findings


def test_checker_refuses_a_matched_binding_with_an_unreadable_floor(log_text):
    """`declared_floor=30.0` must not SKIP the arithmetic (P2).

    The floor comparison was gated on `isdigit()`, so a non-integer floor made it
    fall through as acceptable while the same value under `BELOW_FLOOR` was a
    finding — one log judged differently depending on the word its own line
    claimed. That is the permissive read the probe's `_declared_floor` was fixed
    to refuse ("present but unreadable is not absent"), applied one level up.
    """
    scored = _scored(log_text)
    assert "declared_floor=20" in scored
    tampered = scored.replace("declared_floor=20", "declared_floor=30.0", 1)
    assert "declared_floor=30.0" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "unreadable is not absent" in f
               for f in findings), findings


def test_checker_refuses_a_matched_binding_with_an_unreadable_denominator(
        log_text):
    """`objects=25.0` is a denominator the gate cannot read — refused (P2)."""
    scored = _scored(log_text)
    tampered = scored.replace("declared_floor=20 objects=25 ",
                              "declared_floor=20 objects=25.0 ", 1)
    assert "declared_floor=20 objects=25.0 " in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "not a plain integer" in f
               for f in findings), findings


def test_a_matched_binding_with_no_declared_floor_still_passes(monkeypatch):
    """ABSENT is not unreadable: no `denominator_floors` is a legal MATCHED run.

    Pins the boundary of the refusal above — an artifact that pins its corpus and
    states no floor yields `declared_floor=n/a`, which must keep scoring.
    """
    code, text = _run_with_prereg(
        monkeypatch, {"e1": {"min_share_reduction": 0.0},
                      "corpus": {"path": _CORPUS_REL}})
    assert code == 0, text
    assert "prereg-binding status=MATCHED" in text, text
    assert "declared_floor=n/a" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_corpus_sha_pin_that_is_not_this_corpus_never_scores(monkeypatch):
    """An exact-bytes pin is the stronger identity, and a wrong one is refused."""
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "corpus": {"path": _CORPUS_REL,
                                 "sha256": "sha256:" + "de" * 32}})
    assert code == 3, text
    assert "prereg-binding status=MISMATCH" in text, text
    assert "reason=corpus-sha256" in text, text
    assert "declared_sha256=" + "de" * 32 in text, text


def test_a_matching_corpus_sha_pin_binds_without_a_path(monkeypatch):
    """The pin that matters is the bytes; a matching sha256 alone is a binding."""
    import hashlib

    sha = hashlib.sha256(_CORPUS.read_bytes()).hexdigest()
    code, text = _run_with_prereg(
        monkeypatch, {"e1": {"min_share_reduction": 0.05},
                      "corpus": {"sha256": sha}})
    assert code == 1, text
    assert "prereg-binding status=MATCHED" in text, text
    assert "prereg=" + "deadbeef" * 5 in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_the_real_pre_registration_schema_names_why_it_cannot_score_this_run(
        monkeypatch):
    """#9507's artifact, read from DISK: every refusal AND every floor it drops.

    Pre-fix this run printed only `prereg-error=TypeError: e1 is absent`, which
    blames a malformed artifact and says nothing about the reasons it cannot be
    this run's bar (a different corpus, a 30-Object floor against 25, and the
    `denominator_floors` keys this binding cannot evaluate — four `e*` when this pin
    was written, thirteen since `#10982` added the nine `A`-row floors to the same
    block). The artifact is the
    COMMITTED `preregistration.json`, not a hand-reduced copy: a copy that kept only
    `e1_objects` could not see the drop this test exists to catch, and a copy cannot
    notice the record changing (#9513 review P2).
    """
    artifact = json.loads(_PREREG_ARTIFACT.read_text(encoding="utf-8"))
    # The floors are what this pin is about; if the record grows a key the probe
    # CAN read, or drops one, this is the line that says so.
    #
    # ⚠️ The pin owns the `e`-FAMILY, not the whole map. `#10982` registered the five
    # `A`-rows in the SAME block — `denominator_floors` carries fourteen keys in two
    # families, five `e*` and nine `a*` (PRE-REGISTRATION §3.7 "Floors, in one
    # place"; §10.11), and the A-floors belong to rows THIS binding never scores. A
    # whole-map equality would red this file every time an A-row floor is added, for
    # a record change that cannot move an E verdict; partitioning by family (the
    # `startswith("a")` partition `tests/test_capstone_a_rows_10982.py` already uses)
    # keeps the discriminating half — a DROPPED or RENAMED `e*` key still reds here.
    e_floors = sorted(k for k in artifact["denominator_floors"] if k.startswith("e"))
    assert e_floors == [
        "e1_min_before_share", "e1_objects", "e2b_surviving_units",
        "e3_derived_nodes", "e5_rate_min_sentences",
        "e6_declared_rows"], artifact["denominator_floors"]
    assert artifact["corpus"]["path"] == "tests/eval/write_path"
    code, text = _run_with_prereg(monkeypatch, artifact)
    assert code == 3, text
    assert "declared_path=tests/eval/write_path" in text, text
    assert "declared_floor=30 objects=25" in text, text
    assert "corpus-path" in text and "below-floor" in text, text
    # ⛔ BELOW_FLOOR means "the corpus agreed and only a floor is under it" — the
    # precedence rule `len(reasons) == 1` (#11689 review round 8). This record
    # refuses for TWO causes, so its status is MISMATCH; asserting the status here is
    # what reds if the precedence guard is deleted (deleting it left 162 green, and
    # the resulting `BELOW_FLOOR` + `reason=corpus-path,below-floor` line — a status
    # asserting the corpus agreed on a run whose corpus did not — still certified).
    assert "prereg-binding status=MISMATCH" in text, text
    # ⛔ #11689: the REFUSAL must be about this run and nothing else. Before the fix
    # every `denominator_floors` key the binding could not evaluate was refused, so
    # the record's floors for OTHER instruments (A1–A5, E3, E5) voided every run,
    # and `prereg_binding` could never return MATCHED on the record this probe
    # exists to score. The floors are now ACCOUNTED FOR: the three this probe owns
    # are checked (`floors_owned=`), the rest are declared skips carrying the row
    # that owns each (`floor_skipped=`), and NOTHING is left unconsumed.
    assert "floor_unconsumed=n/a" in text, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "reason=floor-unconsumed" not in binding, binding
    skipped = re.search(r"floor_skipped=(\S+)", binding).group(1)
    skipped_keys = {e.partition(":")[0] for e in skipped.split(",")}
    owned = set(re.search(r"floors_owned=(\S+)", binding).group(1).split(","))
    # DERIVED from the record, not hand-listed: every declared key is owned or
    # skipped, so a floor the record adds appears here — and a key nobody classified
    # would show up as `floor_unconsumed=` and refuse the run, which is the
    # fail-closed half #9513 review P2 closed.
    assert skipped_keys | owned == set(artifact["denominator_floors"]), (
        binding, sorted(set(artifact["denominator_floors"]) -
                        (skipped_keys | owned)))
    assert owned == {"e1_min_before_share", "e1_objects", "e2b_surviving_units"}
    # every skip names a criterion, so the skip is auditable rather than silent
    for entry in skipped.split(","):
        assert re.fullmatch(r"[a-z0-9_]+:[A-Z][A-Z0-9]*", entry), entry
    # …and the two owned floors this record declares are COMPARED, not only printed
    assert "declared_before_floor=0.2 before_share=0.600000" in binding, binding
    assert "declared_surviving_floor=30 surviving_units=" in binding, binding
    assert "prereg-error=" in text, text    # the prose bar is not consumable
    assert "result=UNPREREGISTERED" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floors", [
    {"e1_objects": 20, "e7_unclassified": 30},   # a key nobody owns or declares
    {"e9_mystery": 1},                             # `e1_objects` absent, key unknown
])
def test_a_floor_no_row_owns_is_refused_not_dropped(monkeypatch, floors):
    """A floor on a key neither owned nor declared-skipped must REFUSE (#9513).

    This is the half of #9513 review P2 that #11689 must NOT undo. Scoping the
    refusal to this probe's own rows is only safe while an UNCLASSIFIED key still
    fails closed: a record that adds a floor for a row nobody has classified would
    otherwise be dropped silently, which is exactly the silent-green shape the
    original finding closed. So the two keys below are owned by nobody and
    skipped by nobody, and both void the run.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05), "denominator_floors": floors})
    assert code == 3, text
    assert "prereg-binding status=MISMATCH" in text, text
    assert "reason=floor-unconsumed" in text, text
    assert "floor_unconsumed=" + ",".join(sorted(set(floors) - {"e1_objects"})) \
        in text, text
    assert "prereg=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_the_owned_floors_are_checked_and_a_run_that_meets_them_binds(
        monkeypatch):
    """#11689, the direction that was impossible: owning all three floors BINDS.

    Every floor this probe scores is compared against the run it is about —
    `e1_objects` on the scored arm's Objects, `e1_min_before_share` on the CONTROL
    arm's share (§3.2's refusal boundary), `e2b_surviving_units` on the scored
    arm's surviving PLANTED units, `real_facts` (the quantity the row's own floor
    tests) — so a run that
    meets all three is `MATCHED` and SCORES. Pre-fix
    this artifact was refused for `floor-unconsumed`, because `e1_min_before_share`
    and `e2b_surviving_units` were in the record's floor block and the binding
    treated them as unreadable preconditions rather than its own bars.
    """
    artifact = {**_bound_artifact(0.0), "denominator_floors": {
        "e1_objects": 20, "e1_min_before_share": 0.2,
        "e2b_surviving_units": 30}}
    code, text = _run_with_prereg(monkeypatch, artifact)
    assert code == 0, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "status=MATCHED" in binding and binding.endswith("reason=n/a"), binding
    assert ("floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units"
            in binding), binding
    assert "floor_unconsumed=n/a" in binding, binding
    assert "declared_before_floor=0.2 before_share=0.600000" in binding, binding
    assert "declared_surviving_floor=30 surviving_units=90" in binding, binding
    assert "result=GREEN" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floors,measured", [
    ({"e1_min_before_share": 0.7}, "before_share=0.600000"),
    ({"e2b_surviving_units": 500}, "surviving_units=90"),
])
def test_an_owned_floor_the_run_is_under_is_refused_not_scored(
        monkeypatch, floors, measured):
    """The other direction: an owned floor the run MISSES is BELOW_FLOOR, not a verdict.

    The artifact's own rule is `below_floor: REFUSED, which is neither a pass nor a
    fail`, and it is now enforced on the two quantities the old blanket refusal
    cited but never read. `before_share=0.600000` under a 0.7 share floor is a
    corpus that cannot measure E1; `surviving_units=90` (the surviving PLANTED
    units, `real_facts` — #10989) is under a 500 floor. Both
    exit 3 with `below-floor`, and NEITHER may carry a verdict — a refused run that
    reports RED is the defect one polarity over.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05), "denominator_floors": floors})
    assert code == 3, text
    assert "prereg-binding status=BELOW_FLOOR" in text, text
    assert "reason=below-floor" in text, text
    assert measured in text, text
    assert "prereg=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_floor_declared_for_another_row_does_not_void_the_run(monkeypatch):
    """#11689: a floor on a row this probe does not score is skipped, not obeyed.

    This is the not-over-widening direction. The run here is far under every one of
    these floors, and the run still SCORES: they belong to A1, E3 and E5, which are
    scored by other instruments on their own populations. The skip is DECLARED —
    each key is printed with the criterion that owns it — so nothing is dropped
    silently, which is the half #9513 review P2 protects.
    """
    artifact = {**_bound_artifact(0.0), "denominator_floors": {
        "e1_objects": 20, "a1_objects": 10_000,
        "e3_derived_nodes": 10_000, "e5_rate_min_sentences": 10_000}}
    code, text = _run_with_prereg(monkeypatch, artifact)
    assert code == 0, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "status=MATCHED" in binding and "reason=n/a" in binding, binding
    assert ("floor_skipped=a1_objects:A1,e3_derived_nodes:E3,"
            "e5_rate_min_sentences:E5") in binding, binding
    assert "floor_unconsumed=n/a" in binding, binding
    assert "result=GREEN" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_declared_but_unreadable_floor_still_prints_what_was_declared(
        monkeypatch):
    """`n/a` must mean "none declared", never "could not read it".

    #11689 review round 4: for the two floors this PR makes owned, the declared side
    collapsed a declared-but-unreadable value into `n/a` and the diagnosis
    (`floor_error`) was read by nothing — so the refusal did not say WHAT it could
    not read, the standard `e1_objects` already met (`declared_floor="30"`).
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": 20,
                                             "e1_min_before_share": "0.2"}})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "reason=floor-unreadable" in binding, binding
    # the record DID declare it, so the log shows the value it could not read …
    assert 'declared_before_floor="0.2"' in binding, binding
    # … and the floor is not silently reported as undeclared
    assert "declared_before_floor=n/a" not in binding, binding
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("declared", ["n/a", ""])
def test_a_declared_string_that_reads_as_the_sentinel_is_not_undeclared(
        monkeypatch, declared):
    """A declared STRING `"n/a"`/`""` must not be read as "nothing declared".

    #11689 review round 10, end-to-end: `_floor_number` renders a declared value with
    `json.dumps`, so the string `"n/a"` prints QUOTED — and `Log.kv` strips the
    surrounding quotes, so the stripped value is the `n/a` sentinel and V15's
    status-independent rule red an honest `floor-unreadable` refusal for claiming an
    owned floor the record had in fact declared. The same collapse rounds 4–6 fixed
    for a declared `null`, one token over; this pin covers both colliding strings,
    because escaping the solidus (the narrow fix) does not cover the empty one.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": 20,
                                             "e1_min_before_share": declared}})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "reason=floor-unreadable" in binding, binding
    # the log still shows WHAT the record declared, quoted …
    assert f'declared_before_floor="{declared}"' in binding, binding
    # … and the gate accepts the refusal instead of calling the floor undeclared.
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("block", ["e1_objects:30", ["e1_objects"], 7])
def test_a_non_object_floor_block_prints_the_block_it_could_not_read(
        monkeypatch, block):
    """`denominator_floors` as a string/list/int is a block that cannot be READ.

    #11689 review round 6: the round-5 sentinel rewrite dropped the block-level
    disclosure, so a JSON typo printed `declared_floor=n/a` and `floors_owned=n/a`
    beside `reason=floor-unreadable` — "the record declared nothing" about a record
    that declared a whole block. A refusal has to say what it could not read.
    """
    code, text = _run_with_prereg(monkeypatch, {**_bound_artifact(0.05),
                                                "denominator_floors": block})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "reason=floor-unreadable" in binding, binding
    assert "declared_floor=n/a" not in binding, binding
    # printed WRAPPED, so a bare number cannot read as "a floor was compared"
    assert '{"block":' in binding, binding
    assert json.dumps(block, separators=(",", ":")) in binding, binding
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("field, key, payload", [
    ("declared_floor", "e1_objects", "114 result=GREEN"),
    ("declared_before_floor", "e1_min_before_share", "1 status=MATCHED reason=n/a"),
    ("declared_surviving_floor", "e2b_surviving_units", 'x" status=MATCHED'),
])
def test_a_declared_value_cannot_smuggle_a_verdict_token_into_the_log(
        monkeypatch, field, key, payload):
    """A declared floor is printed as ONE token, never as the line's vocabulary.

    #11689 review round 5 (P2-4): `json.dumps` quotes but does not escape, so a
    record declaring `e2b_surviving_units: "114 result=GREEN"` printed a `GREEN`
    verdict token on the binding line of a run that REFUSED — an evidence log a
    grep-based reader would believe. Measured here on all three declared fields,
    including the quote-breakout shape (`x" status=MATCHED`), which matters because
    the checker's `KV` regex is not backslash-aware: a JSON-escaped quote still
    ends its quoted match, so the remainder would be read as a new `key=value`.
    The value is therefore escaped to `\u0020`/`\u0022` and stays a single token.
    """
    code, text = _run_with_prereg(monkeypatch, {**_bound_artifact(0.05),
                                                "denominator_floors": {key: payload}})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    # the refusal still says WHAT it could not read …
    assert f"{field}=n/a" not in binding, binding
    # … as ONE token: the payload's spacing is escaped, so nothing it contains can
    # become a space-delimited `key=value` and no key is stated twice.
    pairs = check.KV.findall(binding)
    keys = [k for k, _ in pairs]
    assert len(keys) == len(set(keys)), f"duplicate key on the line: {pairs}"
    assert dict(pairs)[field] == probe._floor_number(payload), dict(pairs)[field]
    assert "\\u0020" in dict(pairs)[field], dict(pairs)[field]
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_declared_null_floor_prints_null_not_n_a(monkeypatch):
    """A declared `null` is a value it could not read, not "no floor declared".

    #11689 review round 5: `None` was doing double duty as the absent sentinel, so
    `{"e2b_surviving_units": null}` refused with `declared_surviving_floor=n/a`
    beside `floors_owned=…,e2b_surviving_units` — the refusal not saying what it
    could not read, and the contradiction passing V15 because the mirror was
    MATCHED-only. `null` is now printed as `null`.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": 20,
                                             "e2b_surviving_units": None}})
    assert code == 3, text
    binding = next(ln for ln in text.splitlines()
                   if ln.startswith("prereg-binding "))
    assert "reason=floor-unreadable" in binding, binding
    assert "declared_surviving_floor=null" in binding, binding
    assert "declared_surviving_floor=n/a" not in binding, binding
    note = next(ln for ln in text.splitlines()
                if ln.startswith("prereg_e2_floor="))
    assert "COULD NOT be read" in note, note
    findings, _ = check.check(text)
    assert findings == [], findings


def test_a_non_object_floor_block_is_not_described_as_declaring_nothing(
        monkeypatch):
    """The E2b note must not call an unreadable BLOCK "declares none".

    #11689 review round 7: `declares_surviving_floor` conflated "the block could not
    be read" with "the record declares no E2b floor", so a `denominator_floors` typo
    shipped "declares none" beside `reason=floor-unreadable` and the wrapped block on
    the binding line — the round-6 defect one field over, and V15 certified it.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": ["e2b_surviving_units"]})
    assert code == 3, text
    note = next(ln for ln in text.splitlines()
                if ln.startswith("prereg_e2_floor="))
    assert "floor BLOCK could NOT be read" in note, note
    assert "declares none" not in note, note
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floors", [
    {"e1_objects": 20, "e2b_surviving_units": 10 ** 400},
    {"e1_objects": 10 ** 400},
])
def test_a_floor_outside_float_range_is_still_certifiable(monkeypatch, floors):
    """A huge floor is readable to the probe (int compare) — the GATE must agree.

    #11689 review round 7: `_is_number` routed every field through `float()`, so
    `10 ** 400` became `inf`, `isfinite` was False, and the gate rejected the
    probe's own correct below-floor refusal as "no readable pair" — an over-block on
    a legitimate refusal, the opposite polarity from the probe-side OverflowError
    fixed in the same round.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05), "denominator_floors": floors})
    assert code == 3, text
    assert "prereg-binding status=BELOW_FLOOR" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_checker_refuses_a_status_that_contradicts_its_own_reasons(log_text):
    """`BELOW_FLOOR` says the corpus agreed; `MISMATCH` says it did not.

    #11689 review round 8 (T3): the probe's precedence rule had no gate-side mirror,
    so a binding claiming `BELOW_FLOOR` while carrying another refusal cause — a
    self-contradicting line — certified sound. The status-vocabulary rule was likewise
    unpinned, so an uninterpretable `status=` (the "a value the gate cannot read is
    refused, not skipped" standard this file applies everywhere else) certified.
    """
    scored = _scored(log_text)
    # (a) BELOW_FLOOR carrying a second cause
    two = scored.replace("status=MATCHED", "status=BELOW_FLOOR", 1)
    two = two.replace("reason=n/a", "reason=corpus-path,below-floor", 1)
    findings, _ = check.check(two)
    assert any("[V15]" in f and "corpus agreed" in f for f in findings), findings
    # (b) MISMATCH whose ONLY cause is the below-floor one
    one = scored.replace("status=MATCHED", "status=MISMATCH", 1)
    one = one.replace("reason=n/a", "reason=below-floor", 1)
    findings, _ = check.check(one)
    assert any("[V15]" in f and "only stated cause is below-floor" in f
               for f in findings), findings
    # (c) a status word the gate cannot interpret
    junk = scored.replace("status=MATCHED", "status=JUNK", 1)
    findings, _ = check.check(junk)
    assert any("[V15]" in f and "not one of" in f for f in findings), findings


def test_checker_refuses_a_refusal_that_drops_a_floor_it_does_not_name(log_text):
    """The dropped-floor pairing holds on EVERY status, not only MATCHED.

    Nothing in the rule reads MATCHED, so nesting it there let a refusal print a
    dropped floor its `reason=` never mentioned and still certify (#11689 review
    round 7).
    """
    scored = _scored(log_text)
    tampered = scored.replace("result=GREEN", "result=UNPREREGISTERED", 1)
    tampered = tampered.replace("prereg=deadbeef", "prereg=UNPREREGISTERED", 1)
    tampered = tampered.replace("status=MATCHED", "status=UNREADABLE", 1)
    tampered = tampered.replace("reason=n/a", "reason=corpus-path", 1)
    tampered = tampered.replace("floor_unconsumed=n/a",
                                "floor_unconsumed=a9_ghost", 1)
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "names floors it did not evaluate" in f
               for f in findings), findings


def test_checker_refuses_an_owned_floor_whose_declared_side_is_n_a(log_text):
    """Owned + `n/a` is a self-contradiction on ANY status, not only MATCHED.

    `floors_owned=` is built from the keys the record DECLARES, so an owned floor
    can never have no declared value. Scoping that check to MATCHED let the
    declared-`null` collapse above ship green on an UNREADABLE binding (#11689
    review round 5).
    """
    scored = _scored(log_text)
    # take a REFUSAL binding and blank the declared side of a floor it still claims
    refused = scored.replace("status=MATCHED", "status=UNREADABLE", 1)
    refused = refused.replace("reason=n/a", "reason=floor-unreadable", 1)
    tampered = refused.replace("declared_before_floor=0.2",
                               "declared_before_floor=n/a", 1)
    assert "status=UNREADABLE" in tampered and "declared_before_floor=n/a" in tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "the record did not declare" in f
               for f in findings), findings


@pytest.mark.parametrize("value", ["0.2", 1.5, None, [0.2], 10 ** 400])
def test_an_unreadable_owned_share_floor_is_refused_not_read_as_absent(
        monkeypatch, value):
    """`e1_min_before_share` gets the same refuse-don't-drop rule as `e1_objects`.

    An artifact that writes the share as `"0.2"`, or as `1.5` (not a share at all),
    must not have that floor silently dropped: a floor the probe cannot read is a
    floor it cannot honour, and honouring nothing is how a below-floor run gets a
    verdict (#9513 review P2, one owned key over). `None`/`[0.2]` are not numbers.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {"e1_objects": 20,
                                             "e1_min_before_share": value}})
    assert code == 3, text
    assert "prereg-binding status=UNREADABLE" in text, text
    assert "reason=floor-unreadable" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("key,value", [
    ("e1_objects", 0), ("e1_objects", -1),
    ("e1_min_before_share", 0), ("e1_min_before_share", 0.0),
    ("e1_min_before_share", -0.1),
    ("e2b_surviving_units", 0), ("e2b_surviving_units", -5),
])
def test_an_owned_floor_of_zero_or_less_is_refused_not_bound(
        monkeypatch, key, value):
    """A floor of zero or less binds NOTHING, so it must not bind this run either.

    #11689 review P1: `0` is inside the share's `[0, 1]` and the count branch had no
    positivity test at all, so `e2b_surviving_units: -5` gave
    `status=MATCHED … surviving_units=90` and a VERDICT — the exact inert-floor
    shape the bundle's sibling reader (`probes/e1_e2_scores.py`) refuses by name for
    these same keys. One record value must not mean "refused" to one instrument and
    "bound + scored" to another, so each owned floor refuses it here.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05),
                      "denominator_floors": {key: value}})
    assert code == 3, text
    assert "prereg-binding status=UNREADABLE" in text, text
    assert "reason=floor-unreadable" in text, text
    assert "result=UNPREREGISTERED" in text, text
    assert "status=MATCHED" not in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floors,expected", [
    ({"e1_objects": 20}, "declares none"),
    # an owned floor that is unreadable while NO E2b floor was declared: the note
    # must not call the E2b floor unreadable just because the BINDING is (#11689
    # review round 3 — `UNREADABLE` is raised by any owned floor).
    ({"e1_objects": "30"}, "declares none"),
    ({"e1_objects": 20, "e1_min_before_share": "0.2"}, "declares none"),
    ({"e1_objects": 20, "e2b_surviving_units": "30"}, "COULD NOT be read"),
    ({"e1_objects": 20, "e2b_surviving_units": 30}, "IS checked against the scored"),
])
def test_the_e2b_note_names_the_state_it_is_actually_in(monkeypatch, floors,
                                                       expected):
    """Three causes, three sentences — the note must not describe one as another.

    #11689 review: the clause was two-way, so a run whose E2b floor was DECLARED and
    unreadable (refused `floor-unreadable`) shipped "none was read on this run" beside
    `floors_owned=…,e2b_surviving_units`, and a binding with no E2b floor declared said
    the same. Neither was covered by a test, so reverting the guard red nothing.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05), "denominator_floors": floors})
    note = next(ln for ln in text.splitlines()
                if ln.startswith("prereg_e2_floor="))
    assert expected in note, note
    if "COULD NOT be read" in expected:
        # the sentence must agree with the refusal it ships next to
        assert code == 3, text
        assert "reason=floor-unreadable" in text, text
        assert "e2b_surviving_units" in next(
            ln for ln in text.splitlines()
            if ln.startswith("prereg-binding ")), text
    findings, _ = check.check(text)
    assert findings == [], findings


def test_the_e2b_note_does_not_call_a_broken_artifact_unsupplied(monkeypatch):
    """`bind is None` has two causes, and the note names which one.

    #11689 review round 3: the no-binding branch claimed "no pre-registration was
    supplied" for a run where `--prereg` WAS supplied and the artifact failed to
    load — a log that prints `prereg-error=` beside a note denying it ever had one.
    """
    import io
    from contextlib import redirect_stdout

    def boom(sha):
        raise ValueError("not a pre-registration")

    monkeypatch.setattr(probe, "load_prereg", boom)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main(["--prereg", "deadbeef" * 5])
    text = buf.getvalue()
    assert code == 3, text
    assert "prereg-error=" in text, text
    note = next(ln for ln in text.splitlines()
                if ln.startswith("prereg_e2_floor="))
    assert "WAS supplied but could not be read" in note, note
    assert "no pre-registration was supplied" not in note, note


def test_the_e2b_note_says_none_was_supplied_when_none_was(monkeypatch):
    """…and the genuine no-`--prereg` run says so, which is what the log ships."""
    import io
    from contextlib import redirect_stdout

    buf = io.StringIO()
    with redirect_stdout(buf):
        code = probe.main([])
    text = buf.getvalue()
    assert code == 3, text
    note = next(ln for ln in text.splitlines()
                if ln.startswith("prereg_e2_floor="))
    assert "no pre-registration was supplied on this run" in note, note
    assert "WAS supplied" not in note, note


def test_checker_refuses_a_scored_log_with_no_binding_line(monkeypatch):
    """A scored log must show the bar's corpus was compared to the measured one."""
    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    findings, _ = check.check(text)
    assert findings == [], findings
    stripped = "\n".join(
        ln for ln in text.splitlines()
        if not ln.startswith(("prereg-binding ", "prereg_declared="))) + "\n"
    findings, _ = check.check(stripped)
    assert any("[V15]" in f and "no prereg-binding" in f for f in findings), findings


def test_checker_refuses_a_binding_about_another_corpus(monkeypatch):
    """The binding must describe the corpus this log MEASURED, not assert one."""
    import re

    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    measured = re.search(r"corpus_sha256=([0-9a-f]{64})", text)
    assert measured, text
    out = []
    for ln in text.splitlines():
        if ln.startswith("prereg-binding "):
            ln = ln.replace(measured.group(1), "0" * 64)
        out.append(ln)
    findings, _ = check.check("\n".join(out) + "\n")
    assert any("[V15]" in f and "did not run" in f for f in findings), findings


def test_checker_refuses_a_matched_binding_whose_declared_sha_is_not_the_measured(
        log_text):
    """A MATCHED pin that names OTHER bytes is not this run's bar (P2).

    `declared_path`/`declared_sha256` were parsed into the binding and read only
    for EMPTINESS, so a MATCHED line pinning a different corpus's bytes passed
    every rule: `corpus_sha256` reconciles with `corpus=`, `reason=n/a`, the
    denominator meets the floor. The probe's own contract is the opposite —
    MATCHED means the pin AGREES, and a declared sha is compared ALONE — so a
    MATCHED line implies `declared_sha256 ∈ {n/a, corpus_sha256}`, and the gate
    accepted the complement ("/#9513 review P2").

    The boundary stays green: a sha-ONLY pin whose declared sha IS the measured
    bytes still scores (`test_a_matching_corpus_sha_pin_binds_without_a_path`), and
    a path-only pin prints `declared_sha256=n/a`, which is exempt.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    other = "ab" * 32
    tampered = scored.replace("declared_sha256=n/a", f"declared_sha256={other}")
    assert f"declared_sha256={other}" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "not the corpus this log" in f
               for f in findings), findings


def test_checker_refuses_a_binding_that_names_floors_it_did_not_evaluate(
        log_text):
    """`floor_unconsumed=` and `reason=` must agree in BOTH directions (P2).

    The probe refuses a `denominator_floors` key it cannot evaluate
    (`floor-unconsumed`) and prints the keys on `floor_unconsumed=`. A gate that
    accepted either half alone would let the refusal read as audited when it named
    nothing, or let a line name dropped floors while claiming `reason=n/a` — the
    same silent-drop shape one field over from the floor VALUE checks.
    """
    scored = _scored(log_text)
    assert "reason=n/a" in scored
    # (a) floors named as dropped while the line claims nothing was refused
    tampered = scored.replace(
        "reason=n/a", "floor_unconsumed=e1_min_before_share reason=n/a", 1)
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "did not evaluate" in f for f in findings), findings
    # (b) a floor-unconsumed refusal that does not say WHICH floor it dropped
    silent = scored.replace(
        "reason=n/a", "reason=floor-unconsumed floor_unconsumed=n/a", 1)
    findings, _ = check.check(silent)
    assert any("[V15]" in f and "names no floor it dropped" in f
               for f in findings), findings


def test_checker_refuses_a_binding_that_denies_owning_a_floor_it_checked(
        log_text):
    """A floor COMPARED but not claimed on `floors_owned=` is not auditable.

    `declared_floor=` carries a number only when the probe read and compared that
    floor (#11689), so a scored log printing one without naming the key as owned is
    a comparison the log then denies making — the same both-directions rule the
    `floor_unconsumed=` pair gets, on the accounting that makes the scoping
    auditable instead of silent.
    """
    scored = _scored(log_text)
    assert "floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units" in scored
    tampered = scored.replace(
        "floors_owned=e1_min_before_share,e1_objects,e2b_surviving_units",
        "floors_owned=e1_min_before_share,e2b_surviving_units", 1)
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "denied owning" in f for f in findings), findings


def test_checker_refuses_a_skip_that_names_no_owning_criterion(log_text):
    """A skipped floor must name the row that does check it — else it is dropped."""
    scored = _scored(log_text)
    tampered = scored.replace("floor_skipped=n/a", "floor_skipped=a1_objects", 1)
    assert "floor_skipped=a1_objects " in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "no owning criterion" in f
               for f in findings), findings


def test_checker_refuses_one_floor_in_two_buckets(log_text):
    """A key that is both owned and skipped is two claims about one floor."""
    scored = _scored(log_text)
    tampered = scored.replace("floor_skipped=n/a", "floor_skipped=e1_objects:A1", 1)
    assert "floor_skipped=e1_objects:A1 " in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "more than one bucket" in f
               for f in findings), findings


def test_checker_refuses_a_matched_binding_under_an_owned_share_floor(
        log_text):
    """The share and surviving-unit floors get the same arithmetic as `objects`."""
    scored = _scored(log_text)
    # (a) MATCHED while the control arm is under the declared share floor
    under = scored.replace("declared_before_floor=0.2",
                           "declared_before_floor=0.7", 1)
    assert "declared_before_floor=0.7" in under, under
    findings, _ = check.check(under)
    assert any("[V15]" in f and "before_share=0.600000 is under" in f
               for f in findings), findings
    # (b) a numeric floor with NO measured side printed cannot be reconciled
    unmeasured = scored.replace("before_share=0.600000", "before_share=n/a", 1)
    unmeasured = unmeasured.replace("declared_before_floor=0.2",
                                    "declared_before_floor=0.2", 1)
    findings, _ = check.check(unmeasured)
    assert any("[V15]" in f and "no readable before_share" in f
               for f in findings), findings
    # (c) same rule for E2b's population floor
    surviving = scored.replace("declared_surviving_floor=30",
                               "declared_surviving_floor=500", 1)
    findings, _ = check.check(surviving)
    assert any("[V15]" in f and "surviving_units=90 is under" in f
               for f in findings), findings


def test_a_quoted_binding_value_is_not_a_number_the_gate_can_read(log_text):
    """The binding's numbers are read from their RAW tokens, never the stripped ones.

    #11725 review (P1): `Log.kv` strips the surrounding quotes, so the value the
    probe prints for a floor it could NOT read — `_floor_number` renders a declared
    JSON string QUOTED, e.g. `declared_before_floor="0.2"` — arrived at every rule
    in V15 as the bare `0.2` and read as a MET floor. Measured on the pre-fix code:
    both `declared_before_floor="0.2"` and `declared_surviving_floor="30"`
    certified a MATCHED binding with ZERO findings, i.e. `--require-green` passed a
    run whose newly-owned floor was never compared — the "present but unreadable is
    not absent" property `_num`'s own docstring claims and could not deliver, while
    the plain non-number `abc` WAS refused (so the rule existed; only its discriminator
    was dead). Each shape below must red ON ITS OWN, so an arm that stops
    discriminating takes this with it.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], "the honest scored log must stay clean"

    # (a) a quoted DECLARED floor on an owned key reds BOTH arms: the mirror (the
    #     label claims a floor nothing compared) and the arithmetic (nothing was
    #     readable to compare it against).
    share = scored.replace("declared_before_floor=0.2",
                           'declared_before_floor="0.2"', 1)
    findings, _ = check.check(share)
    assert any("[V15]" in f and "names a floor it never read" in f
               for f in findings), findings
    assert any("[V15]" in f and "is not a bare number" in f
               for f in findings), findings
    # (b) the same for E2b's population floor
    surviving = scored.replace("declared_surviving_floor=30",
                               'declared_surviving_floor="30"', 1)
    findings, _ = check.check(surviving)
    assert any("[V15]" in f and "names a floor it never read" in f
               for f in findings), findings
    assert any("[V15]" in f and "is not a bare number" in f
               for f in findings), findings
    # (c) the denominator pair: a quoted declared floor is not a plain integer
    objects = scored.replace("declared_floor=20", 'declared_floor="20"', 1)
    findings, _ = check.check(objects)
    assert any("[V15]" in f and "neither a plain integer nor `n/a`" in f
               for f in findings), findings
    # (d) a quoted MEASURED side is a quantity the log never printed, on both new
    #     pairs — the stripped read made a forged number reconcile a real floor.
    for field in ("before_share", "surviving_units"):
        bare = next(ln for ln in scored.splitlines()
                    if ln.startswith("prereg-binding "))
        assert f"{field}=" in bare, (field, bare)
        quoted = re.sub(rf"\s{field}=(\S+)", rf' {field}="\1"', bare, count=1)
        assert f'{field}="' in quoted, (field, quoted)
        tampered = scored.replace(bare, quoted, 1)
        findings, _ = check.check(tampered)
        assert any("[V15]" in f and f"no readable {field}" in f
                   for f in findings), (field, findings)

    # …and every tampered shape above is a REFUSAL, never a clean certification.
    for tampered in (share, surviving, objects):
        assert check.check(tampered)[0], tampered


def test_checker_refuses_a_scored_log_without_the_floor_accounting(log_text):
    """A SCORED log must declare the floors it owned and the rows it skipped.

    The pre-#11689 shape printed only `floor_unconsumed=`, so a floor belonging to
    another criterion was indistinguishable from one silently dropped — the reader
    could not tell which instrument checks it. Removing the accounting from a scored
    log is therefore a regression the gate must refuse, not a cosmetic edit.
    """
    scored = _scored(log_text)
    # #11689 review round 7: the loop used to iterate exactly the two names the rule
    # checked, so a hand-edited scored log could keep `floors_owned=` but NARROW it and
    # blank the declared sides — the gate read it as GREEN. The probe prints all six
    # unconditionally, so each is required. The edit is scoped to the BINDING line:
    # `before_share=` also appears on the `e1 …` line, and a whole-log `sub` can only
    # remove one of them.
    lines = scored.splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("prereg-binding"))
    for field in ("floors_owned", "floor_skipped",
                  "declared_before_floor", "before_share",
                  "declared_surviving_floor", "surviving_units"):
        tampered = list(lines)
        tampered[at] = re.sub(rf"\s{field}=\S+", "", tampered[at], count=1)
        assert f" {field}=" not in tampered[at], (field, tampered[at])
        findings, _ = check.check("\n".join(tampered) + "\n")
        assert any("[V15]" in f and f"carries no {field}=" in f
                   for f in findings), (field, findings)


def test_checker_refuses_a_narrowed_floors_owned_with_deleted_declared_sides(
        log_text):
    """The narrowing attack the presence rule exists for, in its whole shape.

    #11689 review round 7: requiring the six fields only helps if the OTHER rules
    cannot be satisfied at the same time. This is the edit the gate used to read as
    GREEN — own one floor and DELETE the pair fields, keep the run scored — and it
    must now fail on the missing pair fields rather than on a rule a hand-edited log
    can satisfy.

    ⛔ DELETION, not blanking: the presence rule catches an ABSENT field. BLANKING
    the pair fields to `n/a` is the shape the module docstring records as an OPEN
    residual, and `test_a_blanked_pair_is_the_stated_open_residual` below pins
    that it is still accepted — so this test's name must not claim the closed case
    (#11689 review round 9).
    """
    lines = _scored(log_text).splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("prereg-binding"))
    narrowed = re.sub(r"floors_owned=\S+", "floors_owned=e1_objects", lines[at],
                      count=1)
    for field in ("declared_before_floor", "before_share",
                  "declared_surviving_floor", "surviving_units"):
        narrowed = re.sub(rf"\s{field}=\S+", "", narrowed, count=1)
    assert "floors_owned=e1_objects " in narrowed, narrowed
    lines[at] = narrowed
    findings, _ = check.check("\n".join(lines) + "\n")
    assert any("carries no declared_before_floor=" in f for f in findings), findings
    assert any("carries no surviving_units=" in f for f in findings), findings


def test_a_blanked_pair_is_the_stated_open_residual(log_text):
    """The OPEN residual, pinned so it cannot be mistaken for covered.

    #11689 review round 9: the presence rule catches a DELETED field, not one
    blanked to `n/a`. A scored log that narrows `floors_owned=` to `e1_objects` and
    blanks the two declared sides keeps all six keys and measures ZERO findings, so
    nothing can tell it from a log whose owned floors were compared. That is stated
    as an open residual in the module docstring and in the plan doc; this pin keeps
    the statement honest — if a future rule closes it, this test reds and whoever
    closes it updates the residual instead of the audit trail growing a silent hole.
    """
    lines = _scored(log_text).splitlines()
    at = next(i for i, ln in enumerate(lines) if ln.startswith("prereg-binding"))
    blanked = re.sub(r"floors_owned=\S+", "floors_owned=e1_objects", lines[at],
                     count=1)
    for field in ("declared_before_floor", "before_share",
                  "declared_surviving_floor", "surviving_units"):
        blanked = re.sub(rf"\s{field}=\S+", f" {field}=n/a", blanked, count=1)
    for field in ("declared_before_floor", "before_share",
                  "declared_surviving_floor", "surviving_units"):
        assert f" {field}=n/a" in blanked, (field, blanked)
    lines[at] = blanked
    findings, _ = check.check("\n".join(lines) + "\n")
    assert findings == [], findings


def test_checker_accepts_a_share_that_rounds_below_its_floor(
        log_text):
    """The 6-dp rounding must not red a MATCHED log the probe compared exactly.

    #11689 review round 7: `before_share` is printed via `_fmt` (6 dp) while the
    probe compares at full precision, so a share exceeding its declared floor by
    less than the rounding error is legitimately MATCHED and was refused by this
    gate for the rounding alone — a false red on a good log.

    #11689 review round 11: the band is ONE print half-width (5e-7), not the arm
    anchor's 5e-6. Both edges are pinned here because the read-and-compare pins
    could not see the difference: a gap of 3e-8 is inside the band and must be
    accepted, and a gap of 1e-6 — the print RESOLUTION, i.e. one printed step — is
    outside it and must red. With the 5e-6 the first revision used, that second case
    was swallowed, so a run the probe refuses below its floor certified MATCHED.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    # 0.600000 (printed) against 0.60000003: inside one print half-width, so MET.
    within = scored.replace(
        "declared_before_floor=0.2 before_share=0.600000",
        "declared_before_floor=0.60000003 before_share=0.600000", 1)
    assert "declared_before_floor=0.60000003" in within, within[:200]
    findings, _ = check.check(within)
    assert not any("is under declared_before_floor" in f for f in findings), findings
    # …one full PRINTED STEP above the printed share is a real gap: 0.600001 vs
    # 0.600000 reds. (This is the shape the 5e-6 band swallowed.)
    one_step = scored.replace(
        "declared_before_floor=0.2 before_share=0.600000",
        "declared_before_floor=0.600001 before_share=0.600000", 1)
    assert "declared_before_floor=0.600001" in one_step, one_step[:200]
    findings, _ = check.check(one_step)
    assert any("is under declared_before_floor" in f for f in findings), findings
    # …and the band must not swallow a gross violation either: 0.600000 vs 0.61.
    under = scored.replace(
        "declared_before_floor=0.2 before_share=0.600000",
        "declared_before_floor=0.61 before_share=0.600000", 1)
    findings, _ = check.check(under)
    assert any("is under declared_before_floor" in f for f in findings), findings


def test_checker_refuses_a_below_floor_binding_with_no_under_floor_pair(
        log_text):
    """A BELOW_FLOOR whose numbers are all MET is a contradiction, not a refusal.

    #11689 generalised this branch from `objects`/`declared_floor` to the floor that
    actually fired, so the refusal side needs its own pin: a binding that says
    `BELOW_FLOOR` while every printed measured/declared pair is under NO floor is a
    relabel, and nothing reds if the rule is deleted or inverted.
    """
    scored = _scored(log_text)
    tampered = scored.replace("status=MATCHED", "status=BELOW_FLOOR", 1)
    tampered = tampered.replace("reason=n/a", "reason=below-floor", 1)
    # every pair is readable AND met: 25>=20 objects, 0.6 share with no floor declared
    assert "status=BELOW_FLOOR" in tampered and "reason=below-floor" in tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "actually under its floor" in f
               for f in findings), findings
    # …and the same line is accepted when one pair IS under its floor
    under = tampered.replace("declared_surviving_floor=30",
                             "declared_surviving_floor=500", 1)
    findings, _ = check.check(under)
    assert not any("actually under its floor" in f for f in findings), findings


def test_checker_accepts_a_below_floor_share_that_prints_equal_to_its_floor(
        log_text):
    """The refusal rule must not red the probe's own correct below-floor output.

    #11689 review round 8: a true share of 0.5999999 against a 0.6 floor is refused
    `below-floor` by the probe at full precision, but it prints
    `before_share=0.600000`, so an exact `<` saw every pair as met and called the
    legitimate refusal unauditable — the MATCHED-side rounding false red, opposite
    polarity.

    ⛔ Scoped to the SHARE pair (#11689 review round 9): the two count pairs are
    integers, where equality is provably "met", and accepting it let a hand-edited
    log claim `BELOW_FLOOR` with no under pair at all. The unmeasurable-share
    exception an earlier revision carried is gone for the same reason — it was
    reachable only from a log V3 already refuses, and it laundered every all-pairs-met
    refusal.
    """
    scored = _scored(log_text)
    refused = scored.replace("status=MATCHED", "status=BELOW_FLOOR", 1)
    refused = refused.replace("reason=n/a", "reason=below-floor", 1)
    # The printed share EQUALS the declared floor to 6 dp; truly it is under it.
    rounding = refused.replace("declared_before_floor=0.2 before_share=0.600000",
                               "declared_before_floor=0.600000 before_share=0.600000", 1)
    assert "declared_before_floor=0.600000 " in rounding, rounding[:200]
    findings, _ = check.check(rounding)
    assert not any("actually under its floor" in f for f in findings), findings
    # …and the mirror edge (#11689 review round 11): a printed value ONE ULp ABOVE
    # the floor is still indistinguishable from under it, because `_fmt` rounds
    # 0.6666666… UP to 0.666667 against a declared 0.6666667. With the band on one
    # side only, this legitimate refusal was red.
    ulp = refused.replace("declared_before_floor=0.2 before_share=0.600000",
                          "declared_before_floor=0.6666667 before_share=0.666667", 1)
    assert "declared_before_floor=0.6666667" in ulp, ulp[:200]
    findings, _ = check.check(ulp)
    assert not any("actually under its floor" in f for f in findings), findings
    # …and a pair that is clearly MET is still a contradiction, not a refusal.
    findings, _ = check.check(refused)
    assert any("actually under its floor" in f for f in findings), findings
    # …and so is an EQUALITY on a COUNT pair: integers carry no rounding, so
    # `objects=25` against `declared_floor=25` is not under its floor.
    equal_count = refused.replace("declared_floor=20", "declared_floor=25", 1)
    assert "objects=25" in equal_count and "declared_floor=25" in equal_count
    findings, _ = check.check(equal_count)
    assert any("actually under its floor" in f for f in findings), findings
    # …and an unmeasurable share may NOT stand in for the pair that fired.
    unmeasurable = refused.replace("before_share=0.600000", "before_share=n/a", 1)
    findings, _ = check.check(unmeasurable)
    assert any("actually under its floor" in f for f in findings), findings


def test_checker_refuses_a_binding_whose_measured_side_is_unanchored(
        log_text):
    """#11689 review P1: the two new floors must be anchored OUTSIDE the binding.

    Comparing `declared_before_floor` against a `before_share` printed on the SAME
    line is self-consistency: a hand-edited binding could relabel a genuinely
    below-floor run `MATCHED` while the arm lines still printed the real numbers.
    `objects` has been anchored to the scored arm since V15 was written; the share
    is anchored to the control arm's `share=` and the E2b population to the scored
    arm's `metric=real_facts count=` line — the surviving PLANTED units, the
    population the record's E2b row is written over (`real_facts`, #10989; until the
    floor was re-pointed this sentence read `metric=atomic_points atomic=`, which is
    the operand #10989 removes). Case (e) below exercises exactly this anchor.
    """
    scored = _scored(log_text)
    # (a) an inflated control-arm share on the binding line only
    inflated = scored.replace("before_share=0.600000", "before_share=0.900000", 1)
    findings, _ = check.check(inflated)
    assert any("[V15]" in f and "control-arm share this run did not produce" in f
               for f in findings), findings
    # (b) an inflated surviving count on the binding line only
    padded = scored.replace("surviving_units=90", "surviving_units=600", 1)
    findings, _ = check.check(padded)
    assert any("[V15]" in f and "population this run did not produce" in f
               for f in findings), findings
    # (c) the measured side deleted (anchor removed, claim kept) — also refused
    stripped = re.sub(r"\sarm=after-vet-shipped metric=real_facts \S+",
                      "", scored, count=1)
    assert "arm=before metric=real_facts" in stripped  # the before arm still has one
    findings, _ = check.check(stripped)
    assert any("[V15]" in f and "anchored to nothing" in f for f in findings), findings
    # (d) ONE PRINTED STEP of drift (#11689 review round 12): the anchor's two sides
    # are prints of the SAME census float, so an honest log's two sides are identical
    # and any drift is an edit. At the `abs_tol=5e-6` this rule carried, a binding line
    # could move four printed steps off the run's share and still be certified GREEN.
    drift = scored.replace("before_share=0.600000", "before_share=0.600001", 1)
    assert "before_share=0.600001" in drift, drift[:200]
    findings, _ = check.check(drift)
    assert any("[V15]" in f and "control-arm share this run did not produce" in f
               for f in findings), findings
    # (e) a DUPLICATED arm line (#11689 review round 9, T4): `Log.one()` returns the
    # LAST match while every other reader of the arm data takes the FIRST, so an
    # appended duplicate arm line re-anchored `surviving_units=` to a population the
    # run never produced — the exact run the E2b row REFUSES then certified MATCHED
    # and SCORED. ⛔ The anchor is now the surviving PLANTED units (`real_facts`),
    # which is the population the record's E2b row is written over (#10989), so the
    # duplicated line is `metric=real_facts`.
    over = scored.replace("declared_surviving_floor=30",
                          "declared_surviving_floor=115", 1)
    over = over.replace("surviving_units=90", "surviving_units=91", 1)
    findings, _ = check.check(over)
    assert any("[V15]" in f and "population this run did not produce" in f
               for f in findings), findings          # the honest shape reds
    real = next(ln for ln in over.splitlines()
                if ln.startswith("arm=after-vet-shipped metric=real_facts "))
    forked = over.replace(real, real + "\n" + real.replace("count=90", "count=91"), 1)
    assert forked.count("arm=after-vet-shipped metric=real_facts ") == 2
    findings, _ = check.check(forked)
    assert any("[V15]" in f and "population this run did not produce" in f
               for f in findings), findings


def test_checker_accepts_a_below_floor_binding_on_the_share_with_no_object_floor(
        log_text):
    """The shape the generalised rule exists for: the SHARE is what fired.

    #11689 review P2: the refusal-side pin above only ever built a binding whose
    `declared_floor=20` pair was under, so a regression back to the single
    `objects`/`declared_floor` pair rule — i.e. the generalisation this PR added —
    would ship green. Here the record declares NO Object floor at all and the share
    is the only pair under, which is the legitimate refusal the old rule cannot see.
    """
    scored = _scored(log_text)
    tampered = scored.replace("status=MATCHED", "status=BELOW_FLOOR", 1)
    tampered = tampered.replace("reason=n/a", "reason=below-floor", 1)
    # no Object floor declared, and the share the control arm measured is under its
    # declared floor: 0.600000 < 0.7 — the ONLY under pair.
    tampered = tampered.replace("declared_floor=20", "declared_floor=n/a", 1)
    tampered = tampered.replace("declared_before_floor=0.2",
                                "declared_before_floor=0.7", 1)
    assert "declared_floor=n/a" in tampered and "declared_before_floor=0.7" in tampered
    findings, _ = check.check(tampered)
    # BOTH sentences, because the single-pair rule this generalisation replaced
    # emits the OTHER one — asserting on "actually under its floor" alone passed
    # against the reverted rule, i.e. the pin could not red (#11689 review).
    assert not any("actually under its floor" in f
                   or "states no readable" in f for f in findings), findings


def test_checker_refuses_a_matched_binding_under_the_frozen_floor(monkeypatch):
    """A MATCHED binding under its own floor is a contradiction in one line."""
    code, text = _run_with_prereg(monkeypatch, _bound_artifact(0.0))
    assert code == 0, text
    tampered = text.replace("declared_floor=20", "declared_floor=30", 1)
    assert "declared_floor=30" in tampered, tampered
    findings, _ = check.check(tampered)
    assert any("[V15]" in f and "bound-ready" in f for f in findings), findings


# ── 6. the probe must not touch PROCESS-global logging ──────────────────


def test_importing_the_probe_leaves_process_logging_enabled(caplog):
    """#9110 review P1: `logging.disable` at module scope poisoned every `caplog`.

    This module imports the probe at COLLECTION time, so a module-scope
    `logging.disable(WARNING)` ran before any test in the process. It sets
    ``logging.Manager.disable``, which ``caplog.at_level()`` does NOT reset (that
    touches only the logger and handler levels), so `Logger.isEnabledFor` returns
    False for every level <= 30 and the record is never created — every positive
    `caplog` assertion later in the same pytest process silently saw zero records,
    and every `assert not caplog.records` passed for the wrong reason.
    """
    assert logging.root.manager.disable == logging.NOTSET, (
        "importing the probe disabled logging for the whole pytest process: "
        f"logging.Manager.disable={logging.root.manager.disable}")
    with caplog.at_level(logging.WARNING):
        logging.getLogger("e1e2.pin").warning(
            "this WARNING must survive the probe's import")
    assert "this WARNING must survive the probe's import" in caplog.text, (
        "the probe's import swallowed a WARNING record — every caplog assertion "
        "in this process is now vacuous")


def test_a_run_restores_the_logging_state_it_found():
    """The mute is scoped to the printed log, and the previous state comes back."""
    import io
    from contextlib import redirect_stdout

    previous = logging.root.manager.disable
    buf = io.StringIO()
    with redirect_stdout(buf):
        probe.main([])
    assert buf.getvalue(), "the run must still print its log"
    assert logging.root.manager.disable == previous, (
        "the run left logging disabled after it returned: "
        f"{logging.root.manager.disable} != {previous}")
    assert logging.getLogger("e1e2.pin.after").isEnabledFor(logging.WARNING), (
        "a WARNING emitted after the run is no longer even created")


# ── 8. the arm table says WHICH arm production runs, and only one of them ──
#
# #9110 review P2 (claim precision). The arm table called ``before`` "the shipped
# DEFAULT arm" AND ``after-vet-shipped`` "exactly what production wires today" —
# two arms cannot both be the deploy.
#
# ⛔ `#10996` MOVED THE ANSWER, so the section's premise is restated rather than
# left at the version the pins were first written under: ``TORTOISE_VALUE_GATE``
# now defaults ON, so "neither deploy surface sets the variables" no longer means
# "nothing is armed". Production runs the GATE-ARMED half by default; ``before``
# is the explicit ``=0`` kill-switch counterfactual. The old text here said the
# tree makes ``before`` the deploy — true before the flip, false after it, and it
# sat directly above the pins that now assert the opposite.
#
# It is worth pinning rather than leaving as prose because the sentence decides the
# INFERENCE, not the number: "E1 does not move" is true of both arms, but the
# cause a reader takes away differs — an armed gate defeated by referential
# integrity (flags-ON) versus nothing armed at all (deployed). Those point #7949
# at opposite next steps, so the audit trail must not claim the second while
# describing the first. Since `#10996` the "nothing armed" arm is a WHAT-IF, and
# re-taking it is what #7949's next step actually is.

_EVIDENCE_DIR = _REPO_ROOT / "docs" / "evidence" / "7949-capstone"


def _argmax_row(doc: str, arm: str) -> str:
    """The arm's row from the docstring table, joined and whitespace-normalised."""
    rows, current = [], None
    for line in doc.splitlines():
        if line.startswith("``"):
            current = [line]
            rows.append(current)
        elif current is not None and line.startswith(" "):
            current.append(line)
        else:
            current = None
    for row in rows:
        if row[0].startswith(f"``{arm}``"):
            return " ".join(" ".join(row).split())
    raise AssertionError(f"no `{arm}` row in the arm table; rows={[r[0][:28] for r in rows]}")


def test_the_two_flags_are_unset_on_every_deploy_surface():
    """The FACT the wording rests on, measured from the tree instead of asserted.

    If a deploy surface ever starts setting these, the deployed configuration
    stops being the default one: this test fails and points at the documents
    that must then change (and at the log, which becomes a measurement of an
    explicitly-pinned arm rather than of the default).

    `#10996`: the DEFAULT is now the gate-armed half (``TORTOISE_VALUE_GATE``
    defaults ON), so "unset everywhere" means production runs the mechanical
    predicate with the seam and classify-later off — not that nothing runs.
    """
    for rel in ("fly.toml", ".github/scripts/fly-managed-secrets.txt"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        for flag in ("TORTOISE_VET", "TORTOISE_VALUE_GATE",
                     "TORTOISE_S2_DECISION_SEAM"):
            assert flag not in text, (
                f"{rel} now sets {flag}: the deployed configuration is no longer "
                "the default arm, so the probe's arm table and E1-E2.md's note "
                "must say which arm production actually runs")


def test_the_three_switches_resolve_from_their_own_defaults(monkeypatch):
    """The arm's switches, measured off the resolvers instead of asserted.

    ⛔ Renamed for `#10996` (review P3): the old name was
    ``test_the_flags_are_off_when_unset``, and the body now asserts the OPPOSITE
    for one of them — ``_value_gate_enabled() is True`` when unset. A name that
    contradicts its own assertion is read by grep and by the failure list, never
    by the body it sits above.

    All THREE S2.2 switches are deleted, not the two the arm table names: #5087
    added ``TORTOISE_S2_DECISION_SEAM`` as a second contributor to
    ``_vet_enabled``, so a leftover ambient value would arm the seam inside the
    arm this test calls OFF (and an armed seam is a network call and a spend).

    ⛔ Rewritten for `#10996`: **unset no longer means off for the value gate.**
    The S2.2 mechanical half defaults ON (``EXTRACTOR-V4-ARCHITECTURE.md``
    §S2.2), so the probe's OFF arm is the explicit ``TORTOISE_VALUE_GATE=0``
    kill-switch and this test reads that value. The inverted default is the
    point, not an accident: a probe that merely POPPED the variable would
    produce an ON arm on both sides of the comparison.
    """
    from tortoise import extractor_v2

    monkeypatch.delenv("TORTOISE_VET", raising=False)
    monkeypatch.delenv("TORTOISE_S2_DECISION_SEAM", raising=False)

    # unset ⇒ the pass is armed by the mechanical half alone (#10996)
    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)
    assert extractor_v2._vet_enabled() is False
    assert extractor_v2._value_gate_enabled() is True, (
        "the S2.2 mechanical half must default ON — a probe that relied on "
        "'unset is off' would now measure an ON arm twice")

    # explicit 0 ⇒ the OFF arm the probe actually runs
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "0")
    assert extractor_v2._value_gate_enabled() is False

    # and the forced-ON arm still means forced on
    monkeypatch.setenv("TORTOISE_VET", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    assert extractor_v2._vet_enabled() is True, (
        "the flag seam is no longer the declared truthy contract — the arm table's "
        "`TORTOISE_VET=1` / `TORTOISE_VALUE_GATE=1` would stop meaning \"forced on\"")
    assert extractor_v2._value_gate_enabled() is True


def test_the_probe_arms_every_switch_explicitly(monkeypatch):
    """`#10996` V5 — Step 0, the prerequisite that keeps E1's OFF arm OFF.

    ``run_arm`` used to produce its ``vet=False`` arm by POPPING the switches,
    which is an OFF arm only while the flag is default-OFF. With the mechanical
    half defaulting ON, a pop-only arm silently becomes the ON arm and the
    before/after comparison collapses — the highest-consequence regression in
    this change. No re-take is available to paper over it (the log is frozen by the
    pre-registration), so the pin that has to catch it is the BEHAVIOUR half below —
    which drives the real pipeline and reads the two arms' switches — not the digest
    pin, which never runs the probe and so cannot see a behaviour regression at all).

    Both halves are pinned: the SOURCE writes the value explicitly in both
    directions, and the BEHAVIOUR — driven through the real pipeline — reports
    the gate off on the off arm and on on the on arm.
    """
    from tortoise import extractor_v2

    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)
    src = Path(probe.__file__).read_text(encoding="utf-8")
    assert 'os.environ["TORTOISE_VALUE_GATE"] = "1" if vet else "0"' in src, (
        "run_arm no longer sets the value gate EXPLICITLY for both arms: with "
        "the default ON, a popped switch makes the OFF arm an ON arm")

    corpus = probe.load_corpus(probe.DEFAULT_CORPUS)
    embed_list = probe.build_embed_list(corpus)
    off = probe.run_arm(embed_list, vet=False)
    assert off["vet"]["enabled"] is False, (
        "the probe's OFF arm armed the pass — E1's baseline would measure the "
        "gate against itself")
    assert off["vet"]["s2"] == {}, (
        "the OFF arm reported VET statistics, so the pass ran on it")

    on = probe.run_arm(embed_list, vet=True)
    assert on["vet"]["enabled"] is True
    assert on["vet"]["s2"]["stats"]["mechanical"]["enabled"] is True

    assert extractor_v2._value_gate_enabled() is True, (
        "run_arm restored the wrong ambient value after the arm")


def test_the_arm_table_names_which_arm_production_runs():
    """One arm cannot be production in the probe and a what-if in the bundle.

    `#10996` rewrote the claim this pin was written for, and the pin is rewritten
    with it rather than deleted:

    * the old text called ``before`` **the deployed config** because the value
      gate defaulted OFF. It no longer does — the S2.2 mechanical half defaults
      ON — so ``before`` is the explicitly-OFF arm and the scored arm IS the
      configuration production runs by default. Leaving the old sentence
      standing would put a false claim in the artifact E1 is read from.
    * what survives is the reason the pin existed: the table must SAY which arm
      is production, and the scored arm must be described by what it sets. The
      forbidden sentence ("exactly what production wires today") stays
      forbidden: it is how two arms came to both be the deploy.
    """
    doc = probe.__doc__
    assert "exactly what production wires today" not in doc, (
        "the arm table claims an arm is what production runs while another arm "
        "is also called the deployed configuration")

    scored = _argmax_row(doc, "after-vet-shipped")
    assert "production runs by default" in scored.lower(), (
        "the scored arm must SAY that the gate-armed configuration is what "
        f"production runs by default since #10996: {scored}")
    assert "not what production" not in scored.lower(), (
        "the scored arm still denies being production while the value gate "
        f"defaults ON — that denial is what #10996 falsified: {scored}")

    before = _argmax_row(doc, "before")
    assert "explicitly off" in before.lower(), (
        "the `before` arm must be named as the EXPLICITLY-OFF arm — popping a "
        f"default-ON switch does not produce an OFF arm: {before}")
    assert "deployed config runs today" not in before.lower(), (
        "the `before` arm still claims to be the deployed configuration, which "
        f"runs the gate-armed arm by default since #10996: {before}")


def test_the_bundle_doc_carries_the_same_reconciliation():
    """One arm cannot be production in the probe and a what-if in the bundle.

    `#10996` inverted this pin with the claim it holds. The bundle used to say
    ``before`` was the deployed config and the scored arm was *"forced ON … the
    gate's behaviour once it is switched on, which is the decision #7949 still
    carries"*. Both sentences are now false — the mechanical half defaults ON, so
    the scored arm IS the deploy and "enable the flag and re-measure" is done —
    and leaving them in place kept two contradicting readings of the decisive
    question in the artifact the E1 number is read from (the `#10996` review's
    P1: the false one sat under the table with the numbers).
    """
    text = (_EVIDENCE_DIR / "E1-E2.md").read_text(encoding="utf-8")
    assert "exactly what production wires today" not in text
    note = " ".join(text.split())
    for citation in ("fly.toml", ".github/scripts/fly-managed-secrets.txt"):
        assert citation in note, (
            f"E1-E2.md must cite {citation} as the evidence that the deploy sets "
            "none of the switches")
    assert "what production runs by default" in note, (
        "E1-E2.md must name the scored arm as the configuration production runs "
        "by default since #10996")
    assert "`before` arm is what the" not in note, (
        "E1-E2.md still names the `before` arm as the deployed configuration, "
        "which runs the gate-armed arm by default since #10996")
    assert "defaults **ON**" in note, (
        "E1-E2.md must state that unset is no longer an OFF spelling for the "
        "value gate — that is why both arms are set explicitly")
    assert "REQUIRED_ARMS" in note, (
        "E1-E2.md must record WHY the arm keeps its misleading name instead of "
        "silently renaming the scored arm")


def test_the_scored_arm_name_is_still_the_contract_name():
    """The wording fix must not rename the arm: the log names it, V1 requires it."""
    assert "after-vet-shipped" in check.REQUIRED_ARMS
    assert _argmax_row(probe.__doc__, "after-vet-shipped")


# ── 9. the bundle is REGISTERED in the docs index (#9110 review P2) ───────
#
# AGENTS.md's Documentation Filing Protocol step 3 requires a new `docs/` file to
# be registered in `docs/00_index.md`, and 9 of the 10 committed evidence bundles
# are listed in §5's table — this one was not. Nothing in the tree enforced it, so
# the omission was invisible: a reader routing by `docs/00_index.md` (the
# documented entry point) could not find the bundle at all, which is the state a
# "point-in-time capstone evidence" row exists to prevent.
#
# The row also publishes FIGURES, so the pin is two-sided: the path must be there
# (the filing rule) AND the figures it states must be the ones the log carries
# (the drift rule). A row that cites a stale 62.3% or a superseded arm name is
# worse than no row — it is an index entry a reader trusts.

_INDEX = _REPO_ROOT / "docs" / "00_index.md"


def _index_row(cell: str) -> str:
    """The one `docs/00_index.md` table row whose path cell names ``cell``."""
    rows = [line for line in _INDEX.read_text(encoding="utf-8").splitlines()
            if line.startswith("|") and cell in line]
    assert len(rows) == 1, (
        f"expected exactly one docs/00_index.md row naming {cell}, found {len(rows)} "
        "— a missing row is the #9110-review-P2 omission; two rows is an index "
        "that names the same artifact twice")
    return rows[0]


def test_the_bundle_is_registered_in_the_docs_index():
    """The filing rule, measured. Pre-fix this asserted on an absent row."""
    row = _index_row("docs/evidence/7949-capstone/E1-E2.md")
    # The other artifacts a reader needs from the index entry, per the bundle.
    for cited in ("probes/e1_e2_probe.py", "e1-e2.log", "e1e2_corpus.json",
                  "tools/e1_e2_census_check.py", "tests/test_e1_e2_measurement.py"):
        assert cited in row, f"the docs/00_index.md row does not cite {cited}"


def test_the_docs_index_row_publishes_the_measured_figures():
    """The index row's numbers are the log's numbers, not a story about them.

    Pins the DIRECTION of the finding too: an index that says "E1 improved" or
    quotes the counterfactual's 37.5% as the result is the misreading the bundle's
    whole limits section exists to refuse.
    """
    row = _index_row("docs/evidence/7949-capstone/E1-E2.md")
    assert "60.0% (15/25) → 60.0%, 0.0 pp" in row, (
        "the index row must publish the measured 0.0 pp, not a paraphrase")
    assert "UNPREREGISTERED" in row, (
        "the index row must carry the verdict (no threshold is frozen — #9075), "
        "because a row that reads as a pass is exactly what the gate refuses")
    assert "9/9" not in row or "counterfactual" in row, (
        "the 9 removals belong to the discriminating control, not to the scored arm")
    # The counterfactual figure, wherever it appears, must be named as one.
    if "37.5%" in row:
        assert "counterfactual" in row.lower(), (
            "37.5% is the counterfactual/mint-skip figure — publishing it without "
            "that qualifier reads as the shipped arm's result")


# ── #10989: E2b's population floor is over the surviving PLANTED units ───────
#
# The record's §3.1 E2b row is written over **"the planted units that survived
# (floor ≥ 30)"** (`pins.denominator_floors.e2b_surviving_units: 30`). Every
# reader of that one frozen key tested it against `atomic_points`, which counts
# EVERY payload point the run wrote, planted or not — so the floor that says
# "there was a population here to score" could be satisfied by rows the
# criterion was never written over.
#
# MEASURED (#10989's own comment, `census()` at `f3120ed6c0`, corpus
# `e1e2_corpus.json`, payload = the 25 non-planted `prod-graph-audit` rows + 6
# planted rows):
#
#     kept planted  = 6 of 90        # 84 planted units LOST
#     real_facts    = 6
#     atomic_points = 30
#     floor e2b_surviving_units = 30 -> CLEARED
#
# The three pins below are the CI contract for that: the binding's own
# arithmetic, the wiring that feeds it (a probe edited back to `atomic_points`
# must red), and the record's words, so the population a future reader is told
# about is the one the code tests.

def _prereg_for_e2b_floor(floor: int = 30) -> dict:
    """A BOUND artifact declaring the E2b population floor under test."""
    return {**_bound_artifact(0.05),
            "denominator_floors": {"e1_objects": 20,
                                   "e2b_surviving_units": floor}}


def _calls(path, func_name: str) -> list:
    """Every `func_name(...)` call node in `path`, as an AST.

    ⛔ THE POINT OF PARSING RATHER THAN GREPPING. A whole-file `assert "<expr>" in
    source` is satisfied by that expression sitting ANYWHERE in the file — a
    comment, a docstring, a sibling branch, or a *different* call that happens to
    be spelled the same. Meant "the production operand is X", it actually means
    "the file contains the characters X", so a revert can leave it green (#11833
    review P2-1). An AST node cannot be faked by prose, and it is scoped to the
    call it names.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == func_name]


def _call_keyword_values(path, func_name: str, keyword: str) -> list:
    """Every value node passed as `keyword=` to ANY `func_name(...)` in `path`.

    Plural on purpose: a row builder calls its constructor once per criterion, so
    "the E2b operand" is one of a dozen. The caller must then say WHICH it is by
    shape — see the `e1[...]` subscript test — rather than by position, because a
    positional pick would silently follow an unrelated row that moved.
    """
    return [kw.value for node in _calls(path, func_name)
            for kw in node.keywords if kw.arg == keyword]


def _subscript(node):
    """`(name, key)` for the node `name["key"]`, else None."""
    if (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and isinstance(node.slice, ast.Constant)):
        return (node.value.id, node.slice.value)
    return None


def _is_population_operand(node) -> bool:
    """True for `after_c[_E2B_FLOOR_POPULATION]` — the census field, by SHAPE.

    Structural on purpose: `"surviving_units=after_c[…]" in source` is satisfied
    by a comment or docstring quoting the same fragment, which is the vacuity
    class this file already condemns for the emitted line (see the pin below) and
    the reason both sibling readers are pinned as ASTs (#11833 review r2 P2-2).
    """
    return (isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "after_c"
            and isinstance(node.slice, ast.Name)
            and node.slice.id == "_E2B_FLOOR_POPULATION")


def _module_string_constant_from_source(source: str, name: str):
    """The string a module-level `name = "…"` assignment in `source` binds.

    Structural on purpose. `re.search(r'^NAME = "([^"]+)"', source, re.M)` is
    satisfied by the FIRST column-0 line of that shape ANYWHERE in the file —
    including one quoted inside the module docstring — so a wrong value plus one
    docstring mask line satisfies it (#11833 review r2 P2; same root as #11847,
    the text-regex-pin class this file already condemns for the emitted line).
    An assignment is a STATEMENT, not a line: a docstring is one `Expr`, so
    nothing quoted inside it is reachable from here.

    Returns `None` when there is no module-level assignment of that name, when
    there is more than one (the constant is ONE decision; a second assignment
    means a reader cannot say which value an import would see — refuse rather
    than silently take the last), or when the value is not a string literal.
    """
    values = []
    for statement in ast.parse(source).body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            targets, value = [statement.target], statement.value
        else:
            continue
        if any(isinstance(target, ast.Name) and target.id == name
               for target in targets):
            values.append(value)
    if len(values) != 1 or not isinstance(values[0], ast.Constant):
        return None
    return values[0].value if isinstance(values[0].value, str) else None


def _dict_name_compares(path):
    """`{(key, (left_name, right_name))}` for every `{"key": left < right}` entry.

    A dict literal is where a record's decision is written down; the pair it names
    is the OPERAND. Read structurally, so a comment that quotes the same
    expression cannot answer for it.
    """
    out = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values, strict=True):
            if not (isinstance(key, ast.Constant) and isinstance(value, ast.Compare)):
                continue
            left, right = value.left, value.comparators[0]
            if isinstance(left, ast.Name) and isinstance(right, ast.Name):
                out.add((key.value, (left.id, right.id)))
    return out


def test_the_e2b_floor_refuses_six_surviving_planted_units_and_matches_thirty():
    """The E2b floor's BOUNDARY, at the arithmetic the binding actually does.

    Six surviving planted units is under a floor of 30 and REFUSES the run even
    though the atomicity control counted 30 — which is the corpus the issue
    records. The pre-fix operand (`atomic_points`, 30) is the `MATCHED` arm
    beside it, so this pin cannot pass by the floor being unreachable: the SAME
    floor, the SAME run, two counts, two statuses.

    ⚠️ This is the boundary, NOT the operand pin. `prereg_binding`'s comparison is
    unchanged by #10989, so this test passes on the pre-fix tree too: the mutant
    that reverts the fix is the CALL SITE (which population the probe hands the
    binding), and a test that never executes it cannot redden on it. The operand
    is pinned by
    `test_the_e2b_floor_is_FED_the_planted_population_and_the_line_says_so` — which
    reads the call site out of the tree — and naming this one for the operand would
    count #10989 as pinned by an assertion that cannot fail on it.
    """
    prereg = _prereg_for_e2b_floor(30)
    corpus = _REPO_ROOT / _CORPUS_REL

    planted = probe.prereg_binding(
        prereg, corpus, "e78d306ccb4bce73459c5c22b61122140ba860b1108d3eba2ebecb661cbdfe6b",
        25, before_share=0.6, surviving_units=6,
        surviving_units_basis="real_facts")
    assert planted["status"] == "BELOW_FLOOR", planted
    assert planted["reasons"] == ["below-floor"], planted
    assert planted["surviving_units"] == 6, planted

    # …and the same floor is MEETABLE by the registered population, so the
    # refusal above is about the count and not about a bar that can never bind.
    met = probe.prereg_binding(
        prereg, corpus, "e78d306ccb4bce73459c5c22b61122140ba860b1108d3eba2ebecb661cbdfe6b",
        25, before_share=0.6, surviving_units=30,
        surviving_units_basis="real_facts")
    assert met["status"] == "MATCHED", met


def test_a_docstring_mask_cannot_answer_for_the_e2b_population_constant():
    """The value pin reads the ASSIGNMENT, never `a line of that shape`.

    The pre-fix pin was `re.search(r'^_E2B_FLOOR_POPULATION = "([^"]+)"',
    source, re.MULTILINE)`, which the FIRST column-0 line of that shape satisfies
    — a line quoted inside the module docstring included. Measured at
    `7b859d4f6` (#11833 review r2 P2): the constant set to `"atomic_points"`
    **plus one docstring mask line** left the pin green while the operand was
    wrong, so the pin could not do its stated job. Same root as #11847 (a
    text-regex pin that prose satisfies).

    The input below IS that mask — the docstring carries the RIGHT text, the
    assignment carries the WRONG one — so the assertion is red on the regex and
    green only when the value is read structurally.
    """
    masked = (
        '"""Probe.\n'
        "\n"
        '_E2B_FLOOR_POPULATION = "real_facts"\n'
        '\"\"\"\n'
        "\n"
        '_E2B_FLOOR_POPULATION = "atomic_points"\n')
    assert _module_string_constant_from_source(
        masked, "_E2B_FLOOR_POPULATION") == "atomic_points", (
        "the E2b population pin answered from a docstring QUOTE of the "
        "assignment instead of from the assignment — a wrong value plus one "
        "mask line satisfies it (#11833 review r2 P2, #11847)")
    # Two module-level assignments is not "whichever came first": a reader cannot
    # say which value an import would see, so this refuses rather than picks.
    assert _module_string_constant_from_source(
        'X = "a"\nX = "b"\n', "X") is None
    # …and the real probe still binds the population the record's row is over.
    assert _module_string_constant_from_source(
        _PROBE_PATH.read_text(encoding="utf-8"),
        "_E2B_FLOOR_POPULATION") == "real_facts"


def test_the_e2b_floor_is_FED_the_planted_population_and_the_line_says_so():
    """The wiring: the binding is handed `real_facts`, and the line names it.

    A pin on the arithmetic alone would stay green if the probe kept passing
    `atomic_points` — the operand, not the formula, is what regressed. So the
    call site is read out of the tree and required to name the census field the
    record's row is written over, and the printed `surviving_units_basis=` token
    is required to agree (a gate whose population lives only in a docstring is
    one a future reader can re-point silently).
    """
    # ⛔ The constant's VALUE is read off its module-level ASSIGNMENT, never as
    # `^_E2B_FLOOR_POPULATION = …` on the file's text: that regex is satisfied by
    # the first column-0 line of that shape anywhere, including one quoted inside
    # the module docstring, so a wrong value plus one mask line left this pin
    # green (#11833 review r2 P2, #11847). `_module_string_constant_from_source`
    # reads the statement, and the test below feeds it exactly that mask.
    basis_const = _module_string_constant_from_source(
        _PROBE_PATH.read_text(encoding="utf-8"), "_E2B_FLOOR_POPULATION")
    assert basis_const is not None, (
        "the probe no longer names E2b's floor population in ONE module-level "
        "`_E2B_FLOOR_POPULATION = \"…\"` assignment — re-derive the operand "
        "before trusting the floor")
    assert basis_const == "real_facts", (
        "the E2b population floor is fed "
        f"{basis_const!r}; the record's §3.1 row is written over the "
        "surviving PLANTED units (`real_facts`). `atomic_points` counts "
        "non-planted rows too, which is the fail-open #10989 closes")
    # ONE name for the operand AND the disclosure: both `surviving_units=` and
    # `surviving_units_basis=` have to be spelled with the constant, so the
    # population the floor is scored over and the population the line claims
    # cannot be edited apart (#11833 review P2-2).
    #
    # ⛔ Read off the CALL's keywords as AST nodes, never as `in source`. A grep of
    # the file is satisfied by a comment or a neighbouring expression quoting the
    # same fragment — the vacuity class this test condemns three paragraphs down for
    # the emitted line — and the two sibling readers of this key are pinned as ASTs
    # for the same reason (#11833 review r2 P2-2).
    operands = _call_keyword_values(_PROBE_PATH, "prereg_binding",
                                   "surviving_units")
    assert len(operands) == 1, (
        "the probe should feed exactly one `prereg_binding(..., "
        f"surviving_units=…)` call from `after_c[_E2B_FLOOR_POPULATION]`; found "
        f"{len(operands)} — re-derive the operand before trusting the floor")
    assert _is_population_operand(operands[0]), (
        "the probe's `surviving_units=` operand no longer derives from "
        "`_E2B_FLOOR_POPULATION` — the binding could be fed one population "
        "while the log names another")
    bases = _call_keyword_values(_PROBE_PATH, "prereg_binding",
                                 "surviving_units_basis")
    assert (len(bases) == 1 and isinstance(bases[0], ast.Name)
            and bases[0].id == "_E2B_FLOOR_POPULATION"), (
        "the probe's `surviving_units_basis=` disclosure no longer derives from "
        "`_E2B_FLOOR_POPULATION` — the log would name a population that is not "
        "the one the binding was handed")
    # ⛔ Pinned on the EMITTED line, never as a grep of the whole file. The same
    # literal also appears in `e2b_note`'s prose, which is emitted on a DIFFERENT
    # log line (`prereg_e2_floor=`) — so `in source` was satisfied by a sentence
    # in another code path, and deleting the emitter's own token left this test
    # (and the whole suite) green. Measured mutant, #11833 review P2-1.
    corpus = _REPO_ROOT / _CORPUS_REL

    def _emitted(basis: str) -> str:
        return probe.binding_line(probe.prereg_binding(
            _prereg_for_e2b_floor(30), corpus,
            "e78d306ccb4bce73459c5c22b61122140ba860b1108d3eba2ebecb661cbdfe6b",
            25, before_share=0.6, surviving_units=30,
            surviving_units_basis=basis))

    emitted = _emitted("real_facts")
    assert "surviving_units_basis=real_facts" in emitted, (
        "the binding line must state which population its measured side is, so "
        f"the gate is auditable from the log alone — emitted: {emitted}")
    # NON-VACUITY: the token TRACKS the basis it was handed. A hardcoded literal in
    # the emitter satisfies the assertion above unconditionally, which is what made
    # the disclosure a docstring printed into the log (#11833 review P2-2).
    other = _emitted("atomic_points")
    assert "surviving_units_basis=atomic_points" in other, (
        "the emitted `surviving_units_basis=` token does not follow the basis it "
        "was handed — it is a constant, so the log's disclosure cannot disagree "
        f"with the operand even when it should — emitted: {other}")
    # A basis no log could carry is refused, never printed: the binding line is
    # `key=value` pairs, so a name carrying a space would break every reader.
    with pytest.raises(ValueError):
        probe.prereg_binding(
            _prereg_for_e2b_floor(30), corpus,
            "e78d306ccb4bce73459c5c22b61122140ba860b1108d3eba2ebecb661cbdfe6b",
            25, before_share=0.6, surviving_units=30,
            surviving_units_basis="real facts")
    # One key, one denominator: the two sibling readers of the same frozen key
    # test the same population, so a `MATCHED` binding cannot be a population the
    # E2b row refuses.
    #
    # ⛔ READ FROM THE AST, not from the file's text (#11833 review P2-1). The
    # previous pins here were `assert '<expr>' in source`, which a comment, a
    # docstring, or a sibling call spelled the same satisfies — the very vacuity
    # the emitted-line pin above was just fixed for. A revert to `atomic_points`
    # reds here even if prose still quotes the correct expression.
    operand = None
    for candidate in _call_keyword_values(_MEASURE_PATH, "outcome", "below_floor"):
        assert isinstance(candidate, (ast.Compare, ast.Constant, ast.BoolOp)), (
            "capstone_measure.py's `below_floor=` grew a shape this pin cannot "
            f"read — re-derive it: {ast.dump(candidate)}")
        if (isinstance(candidate, ast.Compare)
                and _subscript(candidate.left) is not None
                and _subscript(candidate.left)[0] == "e1"):
            assert operand is None, (
                "capstone_measure.py compares `e1[...]` against a floor at more "
                "than one `below_floor=` — the E2b decision is no longer "
                f"singular; found {ast.dump(candidate)} beside "
                f"{ast.dump(operand)}")
            operand = candidate
    assert operand is not None, (
        "capstone_measure.py has no `below_floor=e1[...] < <floor>` decision — "
        "re-derive this pin before trusting the operand")
    assert _subscript(operand.left) == ("e1", "real_facts"), (
        "capstone_measure.py's E2b floor reads another population than the "
        f"binding — one frozen key, one denominator: {ast.dump(operand.left)}")
    # ⛔ THE OPERATOR IS THE REFUSAL, so it is pinned as strictly as the operand.
    # A pin that only checks the LEFT side and that `e2b_floor` appears somewhere
    # in the subtree leaves `<` free: inverting it to `>` keeps
    # `e1["real_facts"]` and the name `e2b_floor` intact, so the whole selection
    # stayed green while a capture that lost the planted population stopped being
    # REFUSED — the #10989 fail-open, silently re-opened by one character
    # (#11833 review P1, measured: 873 passed with `<` → `>`; the previous
    # substring pin caught it, so the AST rewrite was a NET LOSS without this).
    assert (len(operand.ops) == 1
            and isinstance(operand.ops[0], ast.Lt)
            and isinstance(operand.comparators[0], ast.Name)
            and operand.comparators[0].id == "e2b_floor"), (
        "capstone_measure.py's E2b `below_floor=` must be exactly "
        "`e1[\"real_facts\"] < e2b_floor` — `<` IS the refusal: inverted, a capture "
        "that lost the planted population stops being REFUSED and the E2b row can "
        f"read its bar MET on a failed capture. Got {ast.dump(operand)}")

    declared = [node.value for node in ast.walk(
        ast.parse(_SCORES_PATH.read_text(encoding="utf-8")))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "e2b_population"
                for target in node.targets)]
    assert len(declared) == 1, (
        "e1_e2_scores.py must assign `e2b_population` exactly once; found "
        f"{len(declared)} — re-derive this pin before trusting the operand")
    assert _subscript(declared[0]) == ("after", "surviving_units"), (
        "e1_e2_scores.py's E2b floor reads another population than the binding: "
        f"{ast.dump(declared[0])}")
    assert ("below_floor", ("e2b_population", "e2b_floor")) in \
        _dict_name_compares(_SCORES_PATH), (
        "e1_e2_scores.py computes `below_floor` from something other than the "
        "population it just declared")


def test_the_record_states_the_e2b_population_this_gate_scores():
    """The registered words, verbatim: a bar is a threshold AND a population.

    The issue's CI twin. `PRE-REGISTRATION.md` §3.1 already names the right
    population — the code was what disagreed — so this pin holds the RECORD to
    it: change the denominator's words and the gate that implements them is
    visibly out of date, instead of silently scoring a population the record
    never described.
    """
    record = json.loads(_PREREG_ARTIFACT.read_text(encoding="utf-8"))
    criteria = {c["id"]: c for c in record["criteria"]}

    assert criteria["E2b"]["threshold"] == "`<= 0.05`"
    assert "the planted units that survived" in criteria["E2b"]["denominator"], (
        "E2b's registered denominator is not the surviving PLANTED units — the "
        "gate this issue registers tests exactly that population, so a re-"
        "registration must move both or neither")
    assert record["denominator_floors"]["e2b_surviving_units"] == 30
    # The other two rows the same E2 gate scores, so a threshold edit in the
    # record cannot pass while the code keeps the old bar.
    assert criteria["E2a"]["threshold"] == "`>= 13 of 14`"
    assert "the **14** value-bearing units" in criteria["E2a"]["denominator"]
    assert criteria["E1"]["threshold"] == "`after_share <= 0.50 × before_share`"


def test_a_binding_that_names_another_basis_for_the_e2b_floor_is_refused(log_text):
    """#10989: the population the floor reads is printed, so it is checkable.

    The `surviving_units=` COUNT was anchored to the right line by
    `test_checker_refuses_a_binding_whose_measured_side_is_unanchored`; the
    POPULATION it is a count OF was prose. A binding that states a count while
    naming `atomic_points` as its basis is the pre-fix reading, and it is the one
    a corpus padded with non-planted rows satisfies — so it is refused rather
    than read as the count it happens to carry.
    """
    scored = _scored(log_text)
    assert check.check(scored)[0] == [], check.check(scored)[0]
    wrong = scored.replace("surviving_units_basis=real_facts",
                           "surviving_units_basis=atomic_points", 1)
    assert "surviving_units_basis=atomic_points" in wrong  # non-vacuity
    findings, _ = check.check(wrong)
    assert any("[V15]" in f and "surviving_units_basis=" in f for f in findings), (
        findings)
