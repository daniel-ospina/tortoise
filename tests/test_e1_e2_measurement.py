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
7. **The committed log IS what the probe prints** — the corpus is pinned to a
   rebuild and the log is pinned to a run, so an edited log or a probe improved
   without a re-take cannot pass as the bundle's evidence (#9110 review P2).

DB-free: the pipeline runs with a scripted model, ``sdk=None``, no network.
"""
from __future__ import annotations

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

sys.path.insert(0, str(_REPO_ROOT))
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


def test_the_committed_log_passes_the_structural_check():
    findings, _state = check.check(_LOG.read_text(encoding="utf-8"))
    assert findings == [], findings


def test_the_committed_log_is_what_the_probe_prints(run):
    """The log the bundle is ABOUT is the one file in it with no reproduction pin.

    The corpus is pinned (`test_committed_corpus_matches_a_fresh_build`), and the
    structural checker reconciles the committed log WITH ITSELF — so a hand-edited
    log, or a probe improved without re-taking the log, would keep both green. The
    documented re-take command claims the log reproduces; this is what verifies it.

    ``sha=`` is the only genuinely non-reproducible line: ``measurement_sha()``
    reads ``HEAD``, and the log necessarily names the PARENT commit (the one the run
    was taken at) because it cannot name the commit that adds it.
    """
    def strip(text: str) -> str:
        return "\n".join("sha=<masked>" if ln.startswith("sha=") else ln
                         for ln in text.splitlines())

    committed = _LOG.read_text(encoding="utf-8")
    assert strip(run["text"]) == strip(committed), (
        "e1-e2.log is not what the probe prints — re-take it "
        "(`.venv/bin/python docs/evidence/7949-capstone/probes/e1_e2_probe.py \\ "
        "| tee docs/evidence/7949-capstone/e1-e2.log`) and update `Measured at:`"
    )


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
        f"reason=n/a",
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
        "objects=25 floor_unconsumed=n/a reason=n/a",
        "objects=25 floor_unconsumed=n/a reason=floor-unreadable", 1)
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
    tampered = scored.replace("objects=25 reason=n/a", "objects=25.0 reason=n/a", 1)
    assert "objects=25.0 reason=n/a" in tampered, tampered
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
    """#9507's artifact, read from DISK: every refusal AND the four floors it drops.

    Pre-fix this run printed only `prereg-error=TypeError: e1 is absent`, which
    blames a malformed artifact and says nothing about the reasons it cannot be
    this run's bar (a different corpus, a 30-Object floor against 25, and the four
    `denominator_floors` keys this binding cannot evaluate). The artifact is the
    COMMITTED `preregistration.json`, not a hand-reduced copy: a copy that kept only
    `e1_objects` could not see the drop this test exists to catch, and a copy cannot
    notice the record changing (#9513 review P2).
    """
    artifact = json.loads(_PREREG_ARTIFACT.read_text(encoding="utf-8"))
    # The floors are what this pin is about; if the record grows a key the probe
    # CAN read, or drops one, this is the line that says so.
    assert sorted(artifact["denominator_floors"]) == [
        "e1_min_before_share", "e1_objects", "e2b_surviving_units",
        "e3_derived_nodes", "e5_rate_min_sentences"], artifact["denominator_floors"]
    assert artifact["corpus"]["path"] == "tests/eval/write_path"
    code, text = _run_with_prereg(monkeypatch, artifact)
    assert code == 3, text
    assert "declared_path=tests/eval/write_path" in text, text
    assert "declared_floor=30 objects=25" in text, text
    assert "corpus-path" in text and "below-floor" in text, text
    assert ("floor_unconsumed=e1_min_before_share,e2b_surviving_units,"
            "e3_derived_nodes,e5_rate_min_sentences") in text, text
    assert "prereg-error=" in text, text    # the prose bar is not consumable
    assert "result=UNPREREGISTERED" in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


@pytest.mark.parametrize("floors", [
    {"e1_objects": 20, "e1_min_before_share": 0.2},   # 1 of 5 floors consumed
    {"e1_min_before_share": 0.2},                      # `e1_objects` absent
])
def test_a_floor_the_probe_cannot_evaluate_is_refused_not_dropped(
        monkeypatch, floors):
    """A floor on a key the binding cannot check must REFUSE (#9513 review P2).

    `_declared_floor` consumed only `denominator_floors.e1_objects`, so the real
    `capstone-preregistration/v1` record — five floors, including
    `e1_min_before_share: 0.2`, an ANTI-GAMING floor on a number this probe
    measures and prints (`e1 before_share=0.600000`) — bound as `MATCHED` with four
    floors silently dropped: a run whose before-share reduction was 0.06 met
    `e1_objects` and scored with the registration's own floor violated. The second
    shape is the same drop from the key side: "no `e1_objects` in a NON-EMPTY
    mapping" used to read as "this artifact states no floor", which is the silent
    drop the integer check closed one field over. Both shapes used to score; both
    now exit 3 naming the floors they refused.
    """
    code, text = _run_with_prereg(
        monkeypatch, {**_bound_artifact(0.05), "denominator_floors": floors})
    assert code == 3, text
    assert "prereg-binding status=MISMATCH" in text, text
    assert "reason=floor-unconsumed" in text, text
    assert "floor_unconsumed=e1_min_before_share" in text, text
    assert "prereg=UNPREREGISTERED" in text, text
    assert "result=RED" not in text and "result=GREEN" not in text, text
    findings, _ = check.check(text)
    assert findings == [], findings


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


# ── 8. the scored arm is described as a WHAT-IF, never as production ──────
#
# #9110 review P2 (claim precision). The arm table called ``before`` "the shipped
# DEFAULT arm" AND ``after-vet-shipped`` "exactly what production wires today" —
# two arms cannot both be the deploy. The tree says ``before``:
# ``_vet_enabled()`` / ``_value_gate_enabled()`` are
# ``is_truthy(os.environ.get(...))`` and neither deploy surface sets the variables.
#
# It is worth pinning rather than leaving as prose because the sentence decides the
# INFERENCE, not the number: "E1 does not move" is true of both arms (60.0% either
# way), but the cause a reader takes away differs — an armed gate defeated by
# referential integrity (flags-ON) versus nothing armed at all (deployed). Those
# point #7949 at opposite next steps, so the audit trail must not claim the second
# while describing the first.

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

    If a deploy surface ever starts setting these, the deployed arm BECOMES the
    flags-ON arm: this test fails and points at the two documents that must then
    change (and at the log, which becomes a production measurement worth re-taking).
    """
    for rel in ("fly.toml", ".github/scripts/fly-managed-secrets.txt"):
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        for flag in ("TORTOISE_VET", "TORTOISE_VALUE_GATE",
                     "TORTOISE_S2_DECISION_SEAM"):
            assert flag not in text, (
                f"{rel} now sets {flag}: the deployed configuration is the flags-ON "
                "arm, so the probe's arm table and E1-E2.md's note must stop saying "
                "production leaves all of them OFF")


def test_the_flags_are_off_when_unset(monkeypatch):
    """Unset ⇒ off-path, which is why ``before`` is the deploy — measured, not asserted.

    All THREE S2.2 switches are deleted, not the two the arm table names: #5087
    added ``TORTOISE_S2_DECISION_SEAM`` as a second contributor to
    ``_vet_enabled``, so a leftover ambient value would arm the seam inside the
    arm this test calls OFF (and an armed seam is a network call and a spend).
    """
    from tortoise import extractor_v2

    monkeypatch.delenv("TORTOISE_VET", raising=False)
    monkeypatch.delenv("TORTOISE_VALUE_GATE", raising=False)
    monkeypatch.delenv("TORTOISE_S2_DECISION_SEAM", raising=False)
    assert extractor_v2._vet_enabled() is False
    assert extractor_v2._value_gate_enabled() is False
    monkeypatch.setenv("TORTOISE_VET", "1")
    monkeypatch.setenv("TORTOISE_VALUE_GATE", "1")
    assert extractor_v2._vet_enabled() is True, (
        "the flag seam is no longer the declared truthy contract — the arm table's "
        "`TORTOISE_VET=1` / `TORTOISE_VALUE_GATE=1` would stop meaning \"forced on\"")
    assert extractor_v2._value_gate_enabled() is True


def test_the_scored_arm_does_not_claim_to_be_what_production_runs():
    """The pre-fix sentence, verbatim, must not come back."""
    doc = probe.__doc__
    assert "exactly what production wires today" not in doc, (
        "the scored arm claims to be what production runs, while the `before` arm is "
        "also called the shipped default — the deployed config sets none of the "
        "three S2.2 switches")
    row = _argmax_row(doc, "after-vet-shipped")
    assert "forced on" in row.lower(), (
        "the scored arm must say the two flags are FORCED ON (its `TORTOISE_VET=1` "
        f"is an override of a default-OFF flag): {row}")
    assert "not what production" in row.lower(), (
        f"the scored arm must say what it is NOT — production runs with all three OFF: {row}")
    before = _argmax_row(doc, "before")
    assert "DEPLOYED config runs today" in before, (
        f"the `before` arm must be named as the deployed configuration: {before}")


def test_the_bundle_doc_carries_the_same_reconciliation():
    """One arm cannot be production in the probe and a what-if in the bundle."""
    text = (_EVIDENCE_DIR / "E1-E2.md").read_text(encoding="utf-8")
    assert "exactly what production wires today" not in text
    note = " ".join(text.split())
    assert "**forced ON**" in note, (
        "E1-E2.md must say the scored arm has both flags forced ON")
    for citation in ("fly.toml", ".github/scripts/fly-managed-secrets.txt"):
        assert citation in note, (
            f"E1-E2.md must cite {citation} as the evidence that the deploy leaves "
            "all three flags OFF")
    assert "`before` arm is what the" in note, (
        "E1-E2.md must name the `before` arm as the deployed configuration")
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
