"""The capstone pre-registration gate (#9075) — doc assertion + the checks it must fail.

`#7949`'s method constraint is *"pre-register before running — corpus, split, scoring"*. A
registration nobody checks is a document, and a document can be edited after the numbers land. These
tests pin the two halves that make it executable:

  1. the REAL registration (`docs/evidence/7949-capstone/PRE-REGISTRATION.md`) is complete, its E4
     denominator is 15, its split is a partition, and every pin still matches the tree — so a corpus
     change reds this file instead of silently re-basing the thresholds; and
  2. the gate FAILS on each defect it exists to catch — a row missing metric/denominator/threshold, a
     placeholder, a numberless threshold, an absent criterion, a four-edge denominator, a mutated
     pin, and an evidence log that predates the registration (on a hermetic git repo, because the
     ordering claim is about commits and cannot be tested against the live history).

The ordering test builds its own throwaway repo: `--diff-filter=A` epochs are per-repo facts, so a
fixture repo is the only way to assert the failure direction without mutating this one.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools import capstone_preregistration_check as check  # noqa: E402

PREREG_DIR = REPO_ROOT / "docs" / "evidence" / "7949-capstone"
PREREG = PREREG_DIR / "PRE-REGISTRATION.md"

#: The SECOND bundle the gate's own CI step checks (`--dir … --dir …`, #10468). It is measured here
#: because the two #9736/#9738 arms were written for the capstone bundle and, applied
#: unconditionally, red-ed this one for seven things it never said (#10525).
SIBLING_DIR = REPO_ROOT / "docs" / "evidence" / "5087-hypothesis"

#: The ordering half reads COMMIT HISTORY, and CI test jobs check out at `fetch-depth: 1` — a
#: settled, documented property of this repo (`tests/test_lint_tripwire.py`: *"CI test jobs use
#: shallow fetch-depth:1"*). In a depth-1 clone git grafts the tip, so every path reports as added by
#: HEAD and the gate can no longer tell "ordering violated" from "ordering unprovable": it reports
#: the third outcome and these real-artefact PASS assertions SKIP (#9075 review r4 P1). Skipping is
#: the shape the repo's other history-dependent tests use (`tests/test_finding_provenance.py`,
#: `tests/test_9397_about_edge_properties.py`); assuming a full clone red-ed `python-ci-gate`.
requires_history = pytest.mark.skipif(
    check.history_unavailable(),
    reason=(
        "shallow clone (CI test jobs check out at fetch-depth 1): the add-commit epochs the "
        "ordering half reads are unavailable, so the gate reports the ordering as UNPROVABLE"
    ),
)

HEADER = "| id | metric (numerator) | denominator | threshold |\n|---|---|---|---|\n"


def _doc(tmp_path: Path, body: str) -> Path:
    """A minimal document carrying the given rows plus the REAL pins block."""
    real = PREREG.read_text()
    pins_at = real.find(check.PINS_FENCE)
    assert pins_at > 0, "the real registration must carry a pins block"
    pins = real[pins_at:]
    target = tmp_path / check.PREREG_NAME
    target.write_text(f"# test\n\n{body}\n\n{pins}")
    return target


def _rows(*rows: str) -> str:
    return HEADER + "".join(r + "\n" for r in rows)


FULL_ROWS = _rows(
    "| E1 | shape Objects written | all Objects written (floor ≥ 30) | `<= 0.50 × before` |",
    "| E2a | value units surviving | the **14** value units | `>= 13 of 14` |",
    "| E3 | anchored derived nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
    "| E4 | wired+persisted operator edges | the **15** planted operator edges | `= 15 of 15` |",
    "| E5 | narrative-arm marker claims | raw-arm marker claims over **29** sentences "
    "| `>= 0.90` ratio |",
)


# ── the real artefact ───────────────────────────────────────────────────────


@requires_history
def test_the_real_registration_is_complete_and_orders_against_its_logs():
    """The acceptance command, on the committed artefact.

    "Orders against" and not "precedes": `e1-e2.log` predates this file and is covered by the
    bounded exemption (§7), so the property asserted is the one the epic wants — no log is SCORED
    against a threshold written after it — not a bare date comparison history cannot satisfy.
    """
    report = check.run(PREREG_DIR, use_git=True, use_corpus=True)
    assert report["ok"], report["failures"]
    # The base the verdict was measured against is REPORTED (#10463 review cycle 2, finding 5): a
    # green line must not read the same whether it measured the published base or this branch's HEAD.
    # ⛔ …but only where a base was MEASURED (review cycles 3 and 7, findings 2 and F2). In a depth-1
    # checkout there is no history to order, so the ordering arm is the UNPROVABLE third outcome and
    # nothing was measured — asserting a resolved base there would either red a correctly ordered
    # tree or be satisfied by a base the tool never used. The two outcomes are asserted separately
    # rather than lumped into one set of tolerated strings.
    if check.history_unavailable():
        assert report["ordering"] == check.HISTORY_UNAVAILABLE, report["ordering"]
        assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    else:
        assert report["ordering"] == "verified", report["ordering"]
        assert report["ordering_base"] in {"origin/main", "main", "HEAD"}, report["ordering_base"]
    ids = [r["id"] for r in report["rows"]]
    assert "E1" in ids and "E4" in ids and "E5" in ids
    # E2 is allowed to be split; both halves must exist and be complete.
    assert {"E2a", "E2b"} <= set(ids)


def test_the_pre_existing_log_really_is_unscored():
    """REGRESSION (#9075): the exemption is only honest while the log scores nothing.

    If `e1-e2.log` ever gains a scored verdict, the exemption must stop covering it — this test
    fails first, before the gate can be leaned on to score a run measured against no bar.
    """
    pins = check.parse_pins(PREREG.read_text())
    exempt = pins["pre_existing_unscored_logs"]
    assert list(exempt) == ["e1-e2.log"]
    ok, why = check._log_declares_unscored(PREREG_DIR / "e1-e2.log")
    assert ok, why
    assert exempt["e1-e2.log"]["verdict"] == check.UNSCORED


def test_the_real_registration_pins_describe_the_corpus():
    """The denominators are the tree's, re-derived — not a copy that can rot."""
    pins = check.parse_pins(PREREG.read_text())
    derived = check.derive_corpus_facts(pins)
    assert derived["planted_units"] == pins["corpus"]["planted_units"] == 90
    assert derived["planted_operator_edges"] == pins["corpus"]["planted_operator_edges"] == 15
    assert derived["value_bearing_units"] == pins["corpus"]["value_bearing_units"] == 14
    assert (
        derived["hedge_scope_sentences"]
        == pins["corpus"]["hedge_scope_sentences"]
        == 29
    )
    assert derived["fixtures_hash"] == pins["corpus"]["fixtures_hash"]
    assert derived["salience"]["rows"] == 25
    assert derived["salience"]["in_scope"] == 15
    assert derived["salience"]["positively_classified"] == 9
    assert derived["salience"]["deferred"] == 6
    assert derived["salience"]["detected_classes"] == ["definite_description"]


def test_the_value_bearing_class_is_derived_by_rule_and_named_exclusions():
    """14 = digit-bearing anchors (16) minus the two argued exclusions.

    The class may not be hand-picked: if the rule or an exclusion goes stale, the denominator and
    therefore E2a's threshold silently move. This pins the arithmetic the document argues.
    """
    pins = check.parse_pins(PREREG.read_text())
    derived = check.derive_corpus_facts(pins)
    assert derived["missing_exclusions"] == [], (
        "an excluded unit id is absent from the gold — the exclusion is stale"
    )
    assert len(derived["value_bearing_ids"]) == 14
    assert "wp02_lumen_refactor_u_08" not in derived["value_bearing_ids"]
    assert "wp04_aurora_perf_u_04" not in derived["value_bearing_ids"]


def test_the_split_is_a_partition_of_the_seven_sessions():
    pins = check.parse_pins(PREREG.read_text())
    split = pins["split"]
    tune, held = list(split["tune"]), list(split["held_out"])
    derived = check.derive_corpus_facts(pins)
    assert sorted(tune + held) == sorted(derived["sessions"])
    assert len(derived["sessions"]) == 7
    # The held-out half carries the MAJORITY of the hardest class, and BOTH halves exercise it —
    # a split that moved every value unit to one side would prove nothing about the other.
    assert check._half_counts(derived, held)["value_bearing_units"] == 9
    assert check._half_counts(derived, tune)["value_bearing_units"] == 5
    assert check._half_counts(derived, held)["operator_edges"] == 8


# ── the gate fails on the defects it exists to catch ────────────────────────


def test_a_row_missing_a_denominator_fails():
    rows = check.parse_criteria_rows(_rows(
        "| E1 | shape Objects written |  | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    failures = check.check_completeness(rows)
    assert any("E1" in f and "denominator" in f for f in failures), failures


def test_a_placeholder_metric_or_threshold_fails():
    rows = check.parse_criteria_rows(_rows(
        "| E1 | shape Objects written | all Objects (floor ≥ 30) | TBD |",
        "| E2 | to be decided | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    failures = check.check_completeness(rows)
    assert any("E1" in f and "threshold" in f for f in failures), failures
    assert any("E2" in f and "metric" in f for f in failures), failures


def test_a_threshold_without_a_number_or_without_a_comparator_fails():
    rows = check.parse_criteria_rows(_rows(
        "| E1 | shape Objects written | all Objects (floor ≥ 30) | lower than before |",
        "| E2 | value units | the **14** value units | `13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    failures = check.check_completeness(rows)
    assert any("E1" in f and "no number" in f for f in failures), failures
    assert any("E2" in f and "no comparator" in f for f in failures), failures


def test_a_missing_criterion_fails():
    body = _rows(
        "| E1 | shape Objects written | all Objects (floor ≥ 30) | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
    )
    failures = check.check_completeness(check.parse_criteria_rows(body))
    assert any("E5" in f for f in failures), failures


def test_a_four_edge_denominator_is_refused_wherever_it_appears():
    """The 4-edge denominator swung on identical code; it is refused as a RATE, everywhere."""
    rows = check.parse_criteria_rows(_rows(
        "| E1 | shape Objects written | all Objects (floor ≥ 30) | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **4** planted operator edges | `= 4 of 4` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    pins = check.parse_pins(PREREG.read_text())
    failures = check.check_e4_denominator(rows, "we measured 2/4 on identical code", pins)
    assert any("does not state 15" in f for f in failures), failures
    assert any("four-edge denominator rate '2/4'" in f for f in failures), failures


def test_the_real_document_writes_no_four_edge_rate():
    """The history is stated in words — the document never spells the bad rate out."""
    assert not check.FOUR_EDGE_RATE.search(PREREG.read_text())


#: The heading of the bundle README's CHANGES RECORD for the rule the frozen registration states.
#: One constant, so the guard below and the document cannot drift apart on the string they agree on.
_SUPERSESSION_HEADING = "### The ordering rule this bundle's registration states is SUPERSEDED"


def test_the_bundle_records_the_superseded_ordering_rule_its_registration_states():
    """The registration states the PR-internal rule; the bundle must record what the gate does now.

    `PRE-REGISTRATION.md`'s exit list, item 2, tells an author that the commit ADDING the
    registration must be **strictly older** than the commit adding each `*.log` — the sibling-commit
    comparison this gate replaces with BASE ANCESTRY (#10463 item 4, because a squash merge collapses
    the pair into one commit). Both halves of that are load-bearing here:

    * the document may NOT be edited (its bytes are pinned by `preregistration.json` →
      `document_sha256`, and the ruling makes an amendment a new linked record), so the divergence
      cannot be corrected where it is stated; and
    * left uncorrected everywhere, item 2 is an instruction this gate REFUSES: a lane that reads the
      authoritative protocol, orders its two commits inside one PR exactly as criterion 2 asks, and
      lands is turned away by `SAME_PR_REFUSAL` — *"it must land FIRST, in its own PR"* — a PR
      boundary the registration never mentions.

    So the fix is a changes record in the surface that MAY change (this bundle's README, the
    supersession-note shape the 5087 withdrawal uses), and this leg keeps it there: the record is
    found by its heading and must name what it supersedes, the replacement rule, the refusal it adds,
    and the digest pin that forbids the edit. Delete or hollow out the note and this reds; re-state
    the rule by editing the registration instead and the digest pin reds.

    Non-vacuity: at the pre-fix head the README carries no such section, so the heading split below
    finds one piece and this fails.
    """
    registration = PREREG.read_text(encoding="utf-8")
    readme = (PREREG_DIR / "README.md").read_text(encoding="utf-8")

    # The premise, not the defect — the frozen document still states the superseded comparison.
    assert "strictly older" in registration, (
        "the registration no longer states the sibling-commit rule this record supersedes. The "
        "document is digest-pinned, so an amendment here is the silent edit the gate exists to "
        "refuse — restore its bytes rather than deleting this premise")
    assert "in its own PR" not in registration, (
        "the registration now states the PR boundary the gate requires — if that is a real "
        "amendment it is a NEW linked record, not an edit to these pinned bytes")

    parts = readme.split(_SUPERSESSION_HEADING, 1)
    assert len(parts) == 2, (
        f"the bundle README no longer carries its changes record ({_SUPERSESSION_HEADING!r}): "
        "without it the only statement of the ordering rule is the registration's item 2, which "
        "this gate refuses and may not be edited")
    record = parts[1].split("\n## ", 1)[0]
    for phrase in ("merge base", "SAME_PR_REFUSAL", "PRE-REGISTRATION.md", "document_sha256"):
        assert phrase in record, (
            f"the changes record does not name {phrase!r}: it has to say WHAT is superseded "
            "(`PRE-REGISTRATION.md`), WHAT replaces it (the merge base's add-commit), the refusal "
            "a reader will hit (`SAME_PR_REFUSAL`), and why the document is not simply edited "
            "(`document_sha256`)")


#: `tests/<file>::<symbol>` — a TEST cited by name from an evidence bundle, in the form a reader is
#: meant to RUN (`pytest path::symbol`). A bare name in prose is a historical mention, not an
#: address, so it is out of scope. The trailing group is the `-k` glob form
#: (`tests/x.py::test_census_probe_*`), which resolves by PREFIX instead of by exact name.
_BUNDLE_TEST_REF = re.compile(r"(tests/[A-Za-z0-9_/]+\.py)::(test_[A-Za-z0-9_]+)(\*?)")


def _missing_bundle_refs(refs) -> list[str]:
    """`[(test file, symbol[, glob])]` -> the citations their target file does not support.

    An exact symbol must be DEFINED by the named file. A glob (`…::test_census_probe_*`, pytest's
    own `-k` form) must match at least one defined name — a renamed prefix collects nothing, which
    is the same phantom one level out. A missing FILE resolves to an empty set, so a bundle that
    sends a reader to a test file which no longer exists reds here too.
    """
    import ast  # local: the resolve is the whole cost, and one caller does not want it at import

    defined: dict[str, set[str]] = {}
    missing: list[str] = []
    for rel, name, glob in refs:
        if rel not in defined:
            path = REPO_ROOT / rel
            if not path.exists():
                defined[rel] = set()
            else:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
                defined[rel] = {
                    node.name
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                }
        if glob:
            if not any(n.startswith(name) for n in defined[rel]):
                missing.append(f"{rel}::{name}* (matches none of the names that file defines)")
            continue
        if name not in defined[rel]:
            near = sorted(n for n in defined[rel] if name[:24] in n or n[:24] in name)
            missing.append(f"{rel}::{name}" + (f" (did you mean {near[0]}?)" if near else ""))
    return missing


def test_every_test_name_the_evidence_bundles_cite_exists():
    """⛔ A BUNDLE THAT CITES A TEST WHICH DOES NOT EXIST READS AS CONFIRMATION, not as a typo.

    `docs/evidence/7949-capstone/README.md` cited
    `tests/test_s3_prior_ablation.py::test_the_record_pins_the_documents_digest` — a name no test in
    the tree carried. Nothing redded, and the wrong outcome is not a broken link: that section exists
    so a lane reads IT instead of the digest-pinned registration, and its whole argument is "the
    document's bytes are pinned, so do not edit it". `pytest -k test_the_record_pins_the_documents_
    digest` collects ZERO tests, and the green "no tests ran" reads as "the pin holds" — the same
    relay class `test_every_test_name_the_runbook_names_exists` pins for the runbook (#10186 review,
    FINDING 1), one surface over. A second phantom (a `test_the_capstone_change_…` that the file
    never defined) was in the same README's scope paragraph.

    Resolved against the AST, so a name a file defines — or a class that holds it — passes, while a
    name that was renamed, moved or invented reds with the nearest real names.

    Non-vacuity, asserted in-test rather than claimed: the resolver is required to REJECT a name that
    cannot exist, so a version of this pin that resolved everything would red on its own guard.
    """
    phantom = ("tests/test_s3_prior_ablation.py", "test_this_name_deliberately_does_not_exist", "")
    assert _missing_bundle_refs([phantom]), (
        "the resolver accepted a test name no file carries, so this pin would pass over exactly the "
        "phantoms it exists to refuse")

    refs: set[tuple[str, str, str]] = set()
    for path in sorted((REPO_ROOT / "docs" / "evidence").rglob("*")):
        if path.is_file() and path.suffix in {".md", ".json"}:
            refs.update(_BUNDLE_TEST_REF.findall(
                path.read_text(encoding="utf-8", errors="replace")))
    assert refs, (
        "no evidence bundle cites a test by name any more — re-anchor this pin rather than deleting "
        "it; a pin over an empty reference set checks nothing")

    missing = _missing_bundle_refs(sorted(refs))
    assert not missing, (
        "an evidence bundle sends a reader to a test that does not exist — a `-k` filter over a "
        "phantom name collects zero tests and its green \"no tests ran\" reads as confirmation. "
        "Repair the citation to the real symbol (or rename the test): " + "; ".join(missing))


#: The citation the supersession record carried before #10548's r10 review, kept as a literal so the
#: subject guard below can be shown REJECTING it in-test. It is the reason the guard exists: both
#: halves of it are REAL — the file exists and the symbol is defined in it — so the existence pin
#: above passes, while the test it names pins the **5087** bundle's document
#: (`tools/s3_prior_ablation.py`'s `PREREG_REL`) and stays green on an edit to THIS one.
_WRONG_SUBJECT_REF = (
    "tests/test_s3_prior_ablation.py",
    "test_the_frozen_pre_registration_is_still_the_document_its_record_pins",
    "",
)

#: The symbol that pins THIS bundle's `PRE-REGISTRATION.md` bytes against `preregistration.json`
#: without reading history — so it reds on an edit at any checkout depth, unlike the
#: `@requires_history` real-artefact pass above (#10548 r10: a depth-1 shard SKIPS those).
_THIS_BUNDLE_DIGEST_PIN = "test_the_real_record_pins_the_real_document"


def test_the_supersession_record_cites_the_pin_that_guards_this_bundles_bytes():
    """⛔ EXISTENCE IS NOT SUBJECT — the class `test_every_test_name_…_cite_exists` cannot see.

    The changes record below `_SUPERSESSION_HEADING` exists to tell a lane *"do not edit this
    registration; the bytes are pinned, run the pin"*. It cited
    `tests/test_s3_prior_ablation.py::test_the_frozen_pre_registration_is_still_the_document_its_record_pins`
    for that (#10548 r10 F1): a real file, a real symbol, and the WRONG bundle — it pins the 5087
    document, so it is GREEN on an edited 7949 registration. A citation that exists but names the
    wrong subject is worse than no citation: it converts "unchecked" into "checked and green" at
    exactly the moment a lane consults it.

    Scope, deliberately narrow: every `tests/<file>::<test>` the record ADDRESSES must live in THIS
    file, whose `PREREG_DIR` is this bundle, and the record must name the digest pin explicitly.
    A wrong subject is not mechanically inferable from a module's declared bundle roots — this very
    file's sibling `tests/test_s3_prior_ablation.py` mentions `7949-capstone` too, so a
    module-scope check would pass the pre-fix citation. A historical mention belongs in prose,
    unaddressed (no `tests/<file>::` form), which is where the wrong-subject note now sits.

    Non-vacuity, asserted rather than claimed: the pre-fix sentence is reconstructed by
    substituting the old citation back in, and the guard is required to REJECT it.
    """
    readme = (PREREG_DIR / "README.md").read_text(encoding="utf-8")
    parts = readme.split(_SUPERSESSION_HEADING, 1)
    assert len(parts) == 2, f"the changes record is gone ({_SUPERSESSION_HEADING!r})"
    record = parts[1].split("\n## ", 1)[0]

    this_file = f"tests/{Path(__file__).name}"
    good_ref = f"{this_file}::{_THIS_BUNDLE_DIGEST_PIN}"
    wrong_ref = f"{_WRONG_SUBJECT_REF[0]}::{_WRONG_SUBJECT_REF[1]}"

    def cites_a_wrong_subject(text: str) -> list[str]:
        """The addresses in `text` that cannot be pinning THIS bundle's registration."""
        cited = _BUNDLE_TEST_REF.findall(text)
        if not any(f"{rel}::{name}" == good_ref for rel, name, _ in cited):
            return [f"{this_file}::{_THIS_BUNDLE_DIGEST_PIN} (not cited at all)"]
        return [f"{rel}::{name}" for rel, name, _ in cited if rel != this_file]

    # Non-vacuity: rewrite the record the way it read before the fix, and require the refusal.
    assert good_ref in record, (
        f"the changes record no longer cites {good_ref!r} — the pin that reds on an edit to THIS "
        "bundle's registration is what the paragraph promises a reader")
    pre_fix = record.replace(good_ref, wrong_ref)
    assert pre_fix != record, "the rewrite for the non-vacuity leg did not change the record"
    assert cites_a_wrong_subject(pre_fix), (
        "the subject guard ACCEPTED the pre-fix citation it was written to refuse — a guard that "
        "passes over the defect it records is measuring nothing")

    wrong = cites_a_wrong_subject(record)
    assert not wrong, (
        "the changes record addresses a test that is not in the file pinning THIS bundle's "
        "registration — it will read as confirmation while staying green on the edit it warns "
        "against. Cite the pin that guards these bytes, or drop the address and name the old "
        "symbol as prose (a historical mention is not a command to run): " + "; ".join(wrong))


def test_a_mutated_pin_fails_instead_of_re_basing_the_thresholds():
    pins = check.parse_pins(PREREG.read_text())
    pins["corpus"]["planted_units"] = 91
    failures = check.check_pins(pins, check.derive_corpus_facts(pins))
    assert any("corpus.planted_units" in f for f in failures), failures


def test_a_corpus_change_moves_the_fixtures_hash_pin():
    """The pin is the corpus, so a corpus edit is caught even when every count still matches."""
    pins = check.parse_pins(PREREG.read_text())
    pins["corpus"]["fixtures_hash"] = "sha256:" + "0" * 64
    failures = check.check_pins(pins, check.derive_corpus_facts(pins))
    assert any("fixtures_hash" in f for f in failures), failures


def test_a_split_that_is_not_a_partition_fails():
    pins = check.parse_pins(PREREG.read_text())
    pins["split"]["held_out"] = pins["split"]["held_out"][:2]  # drop wp07
    failures = check.check_pins(pins, check.derive_corpus_facts(pins))
    assert any("not a partition" in f for f in failures), failures


def test_an_overlapping_split_fails():
    pins = check.parse_pins(PREREG.read_text())
    pins["split"]["held_out"] = pins["split"]["held_out"] + ["wp01_quarry_debug"]
    failures = check.check_pins(pins, check.derive_corpus_facts(pins))
    assert any("overlap" in f for f in failures), failures


# ── the temporal ordering, on a throwaway repo ──────────────────────────────


def _git(repo: Path, *args: str, epoch: int | None = None) -> None:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.invalid",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
    )
    if epoch is not None:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = f"{epoch} +0000"
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, env=env)


def _repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    # The tool resolves add-commits relative to REPO_ROOT, so the throwaway repo must BE it.
    monkeypatch.setattr(check, "REPO_ROOT", repo.resolve())
    return repo


def _seed_with_the_real_bundle(repo: Path) -> Path:
    """Copy the REAL 7949 bundle — and the model file E3's class-set arm derives from — into `repo`.

    ⛔ The model file is not decoration. `#10525` (#9736/#9738) made E3's denominator follow the
    writer's own `_DERIVATION_REFERENCES_LABELS`, read out of `tortoise/projection/edges.py` under
    `REPO_ROOT`, and an absent file is a `CheckError` — correctly, because a gate that cannot see the
    model would green the rows it exists to refuse. A fixture that copies the bundle's machine record
    (which asks for that basis) but not the model therefore reds on the FIXTURE instead of on what
    the test is about, so the file comes with it: both `check.run` and the CLI derive the class set
    from `REPO_ROOT`, which the depth-1 fixtures point at a throwaway clone.

    The file is COMMITTED, not merely written: a `--depth 1` clone carries only committed content.
    """
    bundle = repo / "docs" / "evidence" / "7949-capstone"
    bundle.mkdir(parents=True, exist_ok=True)
    for item in PREREG_DIR.iterdir():
        if item.is_file():
            (bundle / item.name).write_bytes(item.read_bytes())
    model = repo / check.DERIVATION_LABELS_PATH
    model.parent.mkdir(parents=True, exist_ok=True)
    model.write_bytes((REPO_ROOT / check.DERIVATION_LABELS_PATH).read_bytes())
    return bundle


def _write_and_commit(repo: Path, name: str, epoch: int, body: str = "x\n") -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", f"add {name}", epoch=epoch)
    return target


def test_a_shallow_checkout_reports_unprovable_instead_of_violated(tmp_path, monkeypatch):
    """REGRESSION (#9075 review r4 P1): a depth-1 clone must never produce an ordering VIOLATION.

    CI test jobs check out at `fetch-depth: 1`, where git grafts the boundary commit: `git log
    --diff-filter=A` then reports EVERY path as added by HEAD, so the exempt log's last-touch epoch
    and the registration's add-commit collapse to the SAME number. The pre-fix gate read that as
    "exempted, but it was MODIFIED at <epoch>, which is not strictly before ..." — a false RED on a
    correctly ordered tree, diagnosed as the exact defect the content bound exists to catch.
    """
    origin = _repo(tmp_path, monkeypatch)
    log = _write_log(origin, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(origin, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []  # full history: ordered

    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", f"file://{origin.resolve()}", str(shallow))
    probe = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=str(shallow), capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "true"
    rel = log.relative_to(origin)
    monkeypatch.setattr(check, "REPO_ROOT", shallow.resolve())
    assert check.history_unavailable() is True
    with pytest.raises(check.HistoryUnavailable) as excinfo:
        check.check_ordering(shallow / rel, [shallow / rel], _exempt())
    assert "shallow" in str(excinfo.value)
    # And it must be a CheckError, i.e. the exit-2 "cannot read my subject" contract, not a crash.
    assert isinstance(excinfo.value, check.CheckError)


def test_the_run_report_carries_the_third_ordering_outcome(monkeypatch):
    """The unprovable outcome reaches the report as a claim — never as a failure, never as green."""
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    report = check.run(PREREG_DIR, use_git=True, use_corpus=False)
    assert report["ordering"] == check.HISTORY_UNAVAILABLE
    assert report["run_order_claim"] == check.HISTORY_UNAVAILABLE
    assert not any("MODIFIED at" in f for f in report["failures"]), report["failures"]
    assert report["ok"] is True


def test_the_cli_never_reports_an_unchecked_ordering_as_a_pass(monkeypatch, capsys):
    """Exit 0 is only honest while the output says the ordering was NOT checked (#9075 r4 P1)."""
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    assert check.main(["--dir", str(PREREG_DIR)]) == 0
    out = capsys.readouterr().out
    assert "ordering NOT checked" in out
    assert "the ordering holds" not in out
    assert check.HISTORY_UNAVAILABLE[:20] in out


def test_ordering_passes_when_the_log_comes_after(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log]) == []


def test_ordering_fails_when_the_log_predates_the_registration(tmp_path, monkeypatch):
    """The defect: a log scored against thresholds that were written afterwards."""
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e4-wire.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    # THE DEFAULT — the ruled verdict (#10463 comment 6564634454, implemented by #10548): the log's
    # add-commit must strictly follow the registration's appearance on the base.
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1
    assert "not strictly after" in failures[0], failures
    # THE PROPOSAL (armed only): the ground is ANCESTRY, so a log whose add-commit is a true
    # ancestor is refused on topology rather than on a committer date.
    failures = _armed(prereg, [log])
    assert len(failures) == 1
    assert "is an ANCESTOR of the pre-registration's add-commit" in failures[0], failures


def test_ordering_is_not_satisfied_by_the_same_second(tmp_path, monkeypatch):
    """'Same second' is not 'before' — the two files landing in one commit is not an ordering.

    ⛔ THIS IS THE PIN OF THE RECORDED RULING, RESTORED (#10546 review cycle 17, IS-2). `#10463`
    comment `6564634454` item 1 keeps this verdict: *"Do not relax the guard. Keep 'same second is
    not before — an ordering that cannot be shown is not an ordering.'"* The earlier revision of
    this PR deleted it and shipped a pass-path for exactly this tree, which is the decision
    reversed by DIFF rather than by ruling. It is restored **without any option passed**, so it
    pins the DEFAULT verdict — the one every caller in CI gets. The proposal's own semantics are
    pinned separately, behind `--allow-atomic-disclosure`
    (`test_the_atomic_disclosure_arm_is_opt_in_and_the_ruled_verdict_is_the_default`), so a reader
    can see both and can see that only one of them is reachable by default.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("x\n")
    log.write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "both at once", epoch=1_700_000_000)
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1
    assert "not strictly after" in failures[0], failures
    # And the disclosure arm may not reach it by default: the same tree WITH a complete disclosure
    # still fails, because the arm is disarmed.
    failures = check.check_ordering(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("not strictly after" in f for f in failures), failures


def _atomic_pins(name: str, **overrides) -> dict:
    """A complete atomic-add disclosure for ``name``, as the MACHINE RECORD carries it.

    The disclosure lives in `preregistration.json`, NOT in the pre-registration's pins block: that
    block is inside the document `document_sha256` freezes, so adding to it re-cuts the digest and
    the record then describes a document that post-dates the run (#10529 review cycle 2, P1).

    ⛔ THE DEFAULTS RESOLVE (#10529 review cycle 14, P2). They used to be this repo's real shas
    (`a1075dfc2` / `cfa3de857`), which exist in NO hermetic repo — so every disclosure here named
    a commit its own tree did not have, and `squash_merge`'s resolvability could not be asserted
    against a passing fixture at all. The default is now HEAD of whichever repo `_repo()` installed,
    so a passing default is a disclosure a reader could actually follow; a test that wants an
    unaddressable value passes `squash_merge=` explicitly.

    ⛔ AND THE BRANCH COMMIT IS NOT HEAD (#10529 review cycle 15, P1). It used to be HEAD, i.e. the
    SAME commit as `squash_merge` — which is the one shape a disclosure can never mean (a squash is
    a new commit, not one of the commits it collapsed) and is exactly what a single laundering
    commit writes when it cites itself as its own provenance. A hermetic repo whose pair landed in
    ONE commit genuinely has no collapsed branch behind it, so the default names a commit a squash
    left unreachable — the shape the disclosure field EXPRESSLY allows not to resolve (see
    `test_branch_commits_need_not_resolve_because_a_squash_hides_them`). The faithful shape, whose
    branch commits really do exist, is built by `_real_squash` and checked end to end in
    `test_a_real_squash_disclosure_is_verified_and_a_forged_squash_is_refused`.
    """
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(check.REPO_ROOT),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    entry = {
        "reason": "the bars were committed before any arm ran",
        "branch_commits": ["deadbeef1 the branch commit a squash left unreachable"],
        "squash_merge": f"{head[:9]} the merged commit",
    }
    entry.update(overrides)
    return {name: entry}


def _armed(*args, **kwargs) -> list[str]:
    """``check_ordering`` with the PROPOSED atomic-disclosure arm ARMED (#10546 review cycle 17).

    ⛔ Everything that does not go through this helper runs the DEFAULT — the verdict the recorded
    ruling on `#10463` comment `6564634454` keeps: *"an ordering that cannot be shown is not an
    ordering"*. The disclosure machinery below is this PR's **proposal**, and the reopen that would
    arm it (`6566767704`) is ungranted, so a test that exercises it must say so at the call site.
    Nothing in CI passes `--allow-atomic-disclosure`; the default path is pinned separately, and
    most emphatically by `test_ordering_is_not_satisfied_by_the_same_second`.
    """
    kwargs["allow_atomic_disclosure"] = True
    return check.check_ordering(*args, **kwargs)


def _two_commits_same_second(repo: Path, log_first: bool,
                             epoch: int = 1_700_000_000) -> tuple[Path, Path]:
    """Two DISTINCT commits with the SAME epoch — git can still order them by ANCESTRY.

    Atomicity is identical-SHA, not equal-epoch (#10529 review cycle 4, P1): an `==` epoch test
    admitted the attack shape (log commit is a true ancestor, hidden behind an equal second) and
    accused the honest shape of being one commit.
    """
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    first, second = (log, prereg) if log_first else (prereg, log)
    for target in (first, second):
        target.write_text("x\n")
        _git(repo, "add", str(target.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", f"add {target.name}", epoch=epoch)
    return prereg, log


def test_a_same_second_attack_is_still_a_hard_failure(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-4 P1): the log's commit is a TRUE ancestor, one second shared.

    An equal-epoch rule handed this to the atomic arm, where a disclosure would have waived a real
    ordering violation. Identity + ancestry keeps it a hard failure — and undeclarable.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _two_commits_same_second(repo, log_first=True)
    # The RULED default refuses it too (the log's add-commit is not strictly after the base's).
    failures = check.check_ordering(prereg, [log])
    assert any("not strictly after" in f for f in failures), failures
    # The PROPOSAL refuses it on top of that: an ANCESTRY violation, which no disclosure can waive.
    failures = _armed(prereg, [log])
    assert any("is an ANCESTOR of the pre-registration's add-commit" in f for f in failures), failures
    # A disclosure must not launder it.
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("is an ANCESTOR of the pre-registration's add-commit" in f for f in failures), failures


def test_a_correctly_ordered_same_second_pair_needs_no_disclosure(tmp_path, monkeypatch):
    """The honest shape: two distinct commits, same second, registration the older one.

    The equal-epoch rule called this "the SAME commit" and demanded a disclosure — a false record
    entry, which is the mirror of the attack it was meant to stop (#10529 cycle-4 P1).
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _two_commits_same_second(repo, log_first=False)
    added: list[str] = []
    assert _armed(prereg, [log], None, None, added) == []
    assert added == []  # ordered by ancestry, so nothing was disclosed


def test_a_future_dated_log_that_is_a_true_ancestor_still_hard_fails(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-5 P1): `%ct` is attacker-controlled, so ancestry decides.

    The log's add-commit is a TRUE ANCESTOR of the registration's, but its committer date was set
    LATER. Gating the reverse-ancestry test behind `log_epoch <= prereg_epoch` let that tree pass
    green with no disclosure and no failure — the exact route this guard exists to close.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_500)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    failures = _armed(prereg, [log])
    assert any("is an ANCESTOR of the pre-registration's add-commit" in f for f in failures), failures


def test_a_malformed_disclosure_on_an_unscored_log_is_reported_not_a_crash(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-5 P2): the print path re-read UNVALIDATED JSON and raised.

    `atomic_add_ordering: {log: "yes"}` reached `run()`'s detail comprehension and escaped as
    `AttributeError` — no failure text, nothing from `--json`, and the module's own exit-2 contract
    bypassed on precisely the input the validator promises to refuse.

    ⛔ ARMED (#10546 review cycle 23, ISSUE 1): this ran the RULED path, where neither `atomic_added`
    nor `atomic_detail` is ever written, so the out-param half of the assertion could not fail — it
    asserted a container no reachable code path writes. The arm that owns those out-params is the
    proposed one; the field diagnosis itself is emitted on both paths (validation precedes the
    dispatch), which is why the failure-text assertion below was real all along.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    for entry in ("yes", {"reason": "r", "branch_commits": 5, "squash_merge": "cfa3de857 x"}):
        added: list[str] = []
        detail: dict = {}
        failures = _armed(prereg, [log], None, None, added, {log.name: entry}, detail)
        assert failures, entry
        assert any("not a usable disclosure" in f for f in failures), failures
        assert added == [] and detail == {}, (added, detail)


def test_a_proven_order_is_not_reported_as_a_content_waiver(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-6 P2): a waiver reported where none was granted teaches that they
    are routine. Git PROVED the order, so no arm may record an exemption."""
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_000)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    exempted: list[str] = []
    assert _armed(prereg, [log], None, exempted) == []
    assert exempted == [], exempted


def test_an_unreadable_record_does_not_add_a_wrong_instruction(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-6 P2): 'disclose it under X' is the wrong repair when X cannot be read."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    # `atomic_detail` is a DICT here, as `run()` always passes one — the earlier version of this
    # test passed `None` and so could not see the KeyError the dict path raised (#10529 cycle 7, P1).
    failures = check.check_ordering(prereg, [log], None, None, None, None, {},
                                    record_unreadable=True)
    assert not any("Disclose it under" in f for f in failures), failures
    # And an UNREADABLE record must never be reported as a declaration either.
    added: list[str] = []
    check.check_ordering(prereg, [log], None, None, added, None, {}, record_unreadable=True)
    assert added == [], added


def test_a_content_decided_pass_is_not_dressed_as_a_declaration(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-7 P2): the pass is decided by the LOG'S CONTENT, so a disclosure
    sitting in the same tree decided nothing and must not be reported as the GROUND of it.

    The unscored arm appended to `atomic_added` whenever a disclosure existed, so the CLI claimed
    `the ordering HOLDS BY DISCLOSURE` for a pass that deleting the disclosure leaves unchanged —
    and, because `atomic_detail` was never filled on that arm, the ATOMIC-ADD block that followed
    printed ZERO commit lines. Measured pre-fix: `added == ['e1-e2.log']`, `detail == {}`.

    The waiver stays VISIBLE on the deciding evidence — `ordering_exempt` — so this asserts both
    halves: nothing claimed as a declaration, and the content waiver still reported.

    ⛔ ARMED (#10546 review cycle 23, ISSUE 1). Unarmed, this ran `_check_ordering_ruled`, which has
    no atomic arm and never writes `atomic_added`/`atomic_detail` — so `added == []` was true of any
    tree, including the pre-fix one the docstring says measured `['e1-e2.log']`. The property is a
    property of `_check_ordering_proposed`'s content arm, so that is the arm it now calls.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    added: list[str] = []
    detail: dict = {}
    exempted: list[str] = []
    assert _armed(prereg, [log], None, exempted, added, _atomic_pins(log.name),
                  detail) == []
    assert added == [] and detail == {}, (added, detail)
    assert exempted == [log.name], exempted


def test_a_report_never_claims_a_declaration_the_record_does_not_carry(tmp_path, monkeypatch):
    """An atomic + provably-unscored log with NO disclosure must not be reported as DECLARED.

    ⛔ ARMED (#10546 review cycle 23, ISSUE 1) — same reason as its sibling above: the ruled path
    never writes `atomic_added`, so the assertion held for every possible tree. This is the
    content arm's shape: an atomic pair whose pass is decided by the LOG'S CONTENT, with no
    disclosure in the record to claim as its ground.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    added: list[str] = []
    exempted: list[str] = []
    detail: dict = {}
    assert _armed(prereg, [log], None, exempted, added, None, detail) == []
    assert added == [] and detail == {}      # nothing was declared
    assert exempted == [log.name]            # and the waiver is still visible


def test_an_atomic_disclosure_does_not_cover_a_later_in_place_edit(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-8 P1): the add-commit is immutable, so it cannot see an edit.

    A log re-committed with a rewritten number AFTER the bars passed green with
    `verified (atomic-add DECLARED: …)` — a NEW silent-green path, invisible to every other check
    (`document_sha256` covers only the registration). The disclosure covers the content that
    entered WITH the registration, and nothing else.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo, epoch=1_700_000_000)
    log.write_text("x\nthe number is now 0.100\n")           # an in-place edit, later
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "rewrite the number", epoch=1_700_086_400)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("not an ancestor of" in f for f in failures), failures


def test_a_valid_json_non_object_record_is_a_parse_error_not_a_traceback(tmp_path):
    """`record.get("schema")` on a list/str/int escaped run()/main() as AttributeError (cycle-8 P2)."""
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / check.PREREG_NAME).write_text("x\n")
    for body in ("[]", '"hello"', "42"):
        (bundle / check.MACHINE_RECORD).write_text(body)
        with pytest.raises(check.CheckError):
            check.check_machine_record(bundle, {})


def test_a_backdated_edit_does_not_beat_the_touch_bound(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-9 P1): the touch bound must be TOPOLOGY, not `%ct`.

    The cycle-8 bound compared `max(_touch_epochs(log)) > prereg_epoch` — a `%ct` comparison in a
    module whose own cycle-5 finding says `%ct` is attacker-controlled. The forward-dated rewrite was
    caught; the BACKDATED one passed green. Whether a touching commit is behind the registration is
    a fact about the graph.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo, epoch=1_700_000_000)
    log.write_text("x\nthe number is now 0.100\n")
    _git(repo, "add", str(log.relative_to(repo)))
    # BACKDATED: a committer date far EARLIER than the registration's.
    _git(repo, "commit", "-q", "-m", "rewrite, backdated", epoch=1_600_000_000)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("not an ancestor of" in f for f in failures), failures


def test_a_backdated_readd_cannot_choose_the_pair(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-10 P1): `%ct` may not decide WHICH add-commit is the pair.

    `_added_commits` is sorted by epoch, so `log_adds[0]` — the representative — was chosen by the
    one field this module has ruled attacker-controlled. A delete-and-re-add BACKDATED to look newer
    became the representative, made the pair look ATOMIC, and a disclosure waived a tree whose other
    add-commit is a strict ANCESTOR of the registration — the condition the docstring calls a HARD
    FAIL that no disclosure can waive. The verdict is now taken over EVERY add-commit, fail-closed.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_900)
    log.unlink()
    _git(repo, "rm", "-q", "--cached", str(log.relative_to(repo)))
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    prereg.write_text("x\n")
    log.write_text("x\n")
    _git(repo, "add", ".")
    # BACKDATED so the re-add looks NEWER than the original add.
    _git(repo, "commit", "-q", "-m", "re-add beside the registration", epoch=1_700_000_100)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("is an ANCESTOR of the pre-registration's add-commit" in f for f in failures), failures


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()


def _unordered_add_fixture(repo: Path, *, side_epoch: int, readd: bool = True,
                           readd_sort: str = "after",
                           log_body: str = "x\n") -> tuple[Path, Path, str, str]:
    """A log whose FIRST add-commit is on a lineage that diverged BEFORE the registration.

    `side` branches from the seed, so its add-commit is neither an ancestor nor a descendant of the
    registration's: git can say nothing about which came first — the pair is UNORDERED, which is a
    different state from "early" and the one the predating arm cannot see. It is merged in later.

    `readd=True` then `git rm`s the log and re-adds it AFTER the registration, so that add-commit IS
    a descendant. `readd_sort` grinds the re-add's sha to a chosen side of `side_sha`, because the
    verdict must not depend on the order of the SHA TEXT (the premise cycle 25 fixed by keying the
    pass on every add-commit; it is kept here so a future `sorted(...)[0]` representative reds).

    `log_body` is the ENTRY's content, so a caller can give the log a REAL machine record (e.g.
    `_capstone_body()`, which proves it measured nothing) and then ask what the content arm does
    with it — the shape `test_the_armed_rule_refuses_an_unscored_log_it_cannot_order` needs.
    """
    seed = repo / "README.md"
    seed.write_text("x\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed", epoch=1_700_000_000)
    trunk = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    _git(repo, "checkout", "-q", "-b", "side")
    log = _write_and_commit(repo, "e1-e2.log", epoch=side_epoch, body=log_body)
    side_sha = _head_sha(repo)
    _git(repo, "checkout", "-q", trunk)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_200)
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge the side branch", "side",
         epoch=1_700_000_300)
    if not readd:
        return prereg, log, side_sha, ""
    _git(repo, "rm", "-q", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "delete the log", epoch=1_700_000_400)
    readd_sha = ""
    for attempt in range(40):
        log.write_text("x\n")
        _git(repo, "add", str(log.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", f"re-add {attempt}", epoch=1_700_000_500 + attempt)
        candidate = _head_sha(repo)
        if (candidate > side_sha) if readd_sort == "after" else (candidate < side_sha):
            readd_sha = candidate
            break
        # Undo the attempt so it is not reachable history and NOT an add-commit of the log.
        _git(repo, "reset", "-q", "--hard", "HEAD~1")
    assert readd_sha, f"could not build the shape: the re-add must sort {readd_sort} the side add"
    return prereg, log, side_sha, readd_sha


@pytest.mark.parametrize("readd_sort", ["after", "before"])
def test_a_readd_beside_an_unordered_add_does_not_prove_the_order(tmp_path, monkeypatch,
                                                                 readd_sort):
    """REGRESSION (#10546 review cycle 26, ISSUE 1): a re-add cannot prove an unorderable entry.

    ⛔ THE FAIL-OPEN THIS PINS. Cycle 25 replaced the hex-sorted representative with `any(...)` over
    every add-commit — right about the representative, and one step too far: the pass fired as soon
    as ONE add-commit was a descendant and `continue`d before anything could look at an add-commit
    git cannot order at ALL. On THIS fixture (side add 1700000100 → registration 1700000200 → merge
    → `git rm` → re-add 1700000500) the armed path returned `[]`: certified, no disclosure, though
    the log entered the tree before its thresholds. The identical tree WITHOUT the re-add was
    refused, so the ONLY difference between REFUSED and PASS was the delete-and-re-add — Route B,
    exactly the fabrication `ATOMIC_ADD_KEY` says this guard prevents, and false only of the armed
    path (the ruled path takes `min(add_epochs)`, the OLDEST add, and keeps refusing).

    The proven-order pass now requires EVERY add-commit to be a descendant, and an add-commit that
    is neither ancestor nor descendant is refused BY CAUSE rather than dropped. Both sha orderings
    are exercised so the verdict cannot depend on hex text.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log, side_sha, readd_sha = _unordered_add_fixture(
        repo, side_epoch=1_700_000_100, readd_sort=readd_sort)

    shas = {sha for _, sha in check._added_commits(log)}
    prereg_sha = check._added_commits(prereg)[0][1]
    assert shas == {side_sha, readd_sha}, check._added_commits(log)
    # The pair git CAN prove (the re-add, which is what the pre-fix `any(...)` fired on) vs the entry
    # git cannot say anything about (the side add — unordered in BOTH directions).
    assert check._is_ancestor(prereg_sha, readd_sha)
    assert not check._is_ancestor(side_sha, prereg_sha)
    assert not check._is_ancestor(prereg_sha, side_sha)
    # …and the ordering of the SHA TEXT, which decided the pre-cycle-25 verdict, is irrelevant.
    assert (sorted(shas)[0] == side_sha) is (readd_sort == "after")

    failures = _armed(prereg, [log])
    assert failures, "an add-commit git cannot order was certified"
    assert "UNORDERED" in failures[0], failures
    assert side_sha[:9] in failures[0], failures
    # The RULED default refuses the same tree too (it is the conservative epoch comparison there).
    assert check.check_ordering(prereg, [log]), "the default path must keep refusing this tree"


def test_an_unordered_add_is_refused_without_the_readd_too(tmp_path, monkeypatch):
    """REGRESSION (#10546 review cycle 26, ISSUE 1): the pass may not be re-widened to `any(...)`.

    This is the same tree BEFORE the re-add, and it was already refused pre-fix (the `late` bound
    caught it). Pinned because it is the control for the test above: the fix must not turn the
    refused tree green, and the re-add must not be what decides either verdict.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log, side_sha, _ = _unordered_add_fixture(repo, side_epoch=1_700_000_100, readd=False)
    assert {sha for _, sha in check._added_commits(log)} == {side_sha}
    failures = _armed(prereg, [log])
    assert failures and "UNORDERED" in failures[0], failures
    assert check.check_ordering(prereg, [log]), "the default path must keep refusing this tree"


def test_an_unordered_add_dated_after_the_registration_is_still_refused(tmp_path, monkeypatch):
    """REGRESSION (#10546 review cycle 26, ISSUE 1): the epoch is not the ground — topology is.

    With the side add's committer date set AFTER the registration's, the pre-fix armed path fell
    through the proven-order pass and the `log_epoch <= prereg_epoch` arm without printing anything
    (the epoch arm asks only whether the date looks early), so an unorderable entry dated forward
    was a silent PASS. Refusing the unordered add names the real state instead, which is why the
    arm exists rather than just narrowing the pass to `all(...)`.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log, side_sha, readd_sha = _unordered_add_fixture(repo, side_epoch=1_700_000_300)
    shas = {sha for _, sha in check._added_commits(log)}
    assert shas == {side_sha, readd_sha}, check._added_commits(log)
    prereg_sha = check._added_commits(prereg)[0][1]
    assert not check._is_ancestor(side_sha, prereg_sha)
    assert not check._is_ancestor(prereg_sha, side_sha)
    # The dates alone would let it through: the log's EARLIEST add — `log_epoch`, the one the epoch
    # arm compares — is dated AFTER the registration's.
    prereg_epoch = check._added_commits(prereg)[0][0]
    epochs = dict((sha, epoch) for epoch, sha in check._added_commits(log))
    assert min(epochs.values()) > prereg_epoch
    failures = _armed(prereg, [log])
    assert failures and "UNORDERED" in failures[0], failures


def test_the_armed_rule_refuses_an_unscored_log_it_cannot_order(tmp_path, monkeypatch):
    """REGRESSION (#10546 review at `99d79dd4b`, FINDING 1): the armed contract is NARROWER.

    `_check_ordering_proposed` advertised the RULED path's sentence — "``[]`` when every log is
    either after the registration or provably unscored" — while the UNORDERED refusal (cycle 26,
    ISSUE 1) runs BEFORE the content arm and is deliberately unconditional. So on the armed path a
    log that PROVABLY produced no number was refused with nothing exempted, and the `#9089`
    exemption the function advertises was unreachable for it: the promise and the code disagreed,
    in the direction that reads as a waiver the arm will not grant.

    The narrowing is the repair, not a widening. The content exemption's own topology bound needs a
    snapshot of the log AT the registration (`_blob_at(prereg_sha, …)`, the value `_late_touches`
    compares against) and an off-lineage log has none — `_blob_at` is empty, so EVERY touch commit,
    its entry included, reads as "late", and an ungated "measured nothing" read would be the
    strip-the-number-after-green hole that bound exists to close. Both verdicts are asserted so a
    later lane cannot widen the arm by restoring the promise, and the last assertion pins the
    function's own `__doc__` to the narrowing — which is what reds on the pre-fix text.

    External corroboration for the direction: in OSF registrations a later re-addition or edit of a
    file does not by itself prove the file existed before the registration (help.osf.io/article/
    626) — provenance binds to the ENTRY event, which is exactly what an unordered add-commit
    fails to prove.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log, side_sha, _ = _unordered_add_fixture(
        repo, side_epoch=1_700_000_100, readd=False, log_body=_capstone_body())
    # The content arm has something to grant here, and it is the REAL predicate — not a stub: every
    # numerator/denominator is null and `run.status=REFUSED`.
    assert check._log_declares_no_measurement(log) == (True, "")
    prereg_sha = check._added_commits(prereg)[0][1]
    assert not check._is_ancestor(side_sha, prereg_sha)
    assert not check._is_ancestor(prereg_sha, side_sha)
    # …and there is NO snapshot of it on the registration's ancestry, which is why this path cannot
    # grant what the ruled path grants.
    assert check._blob_at(prereg_sha, log) == ""
    assert check._late_touches(log, prereg_sha), "every touch reads as late for an off-lineage log"

    armed_exempt: list[str] = []
    armed = _armed(prereg, [log], None, armed_exempt)
    assert len(armed) == 1 and "UNORDERED" in armed[0], armed
    assert armed_exempt == [], armed_exempt
    # The RULED path reads the pair on the BASE, where the order IS published, so its content arm is
    # reachable there: the divergence between the two rules is deliberate and is what the docstring
    # must say.
    ruled_exempt: list[str] = []
    assert check.check_ordering(prereg, [log], None, ruled_exempt) == []
    assert ruled_exempt == [log.name], ruled_exempt
    # The contract the function states about ITSELF must carry the narrowing.
    doc = check._check_ordering_proposed.__doc__ or ""
    assert "INSIDE the registration's own lineage" in doc, doc


def test_a_late_edit_cannot_erase_the_disclosure_requirement(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-10 P2): the content arm must not certify "measured nothing" over a
    LATER edit — a post-green edit flipping the verdict is the silent-green class one arm earlier."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    assert _armed(prereg, [log], None, None, None, _atomic_pins(log.name)) == []
    log.write_text("x\nstripped\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "strip the numbers", epoch=1_700_086_400)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("is not an ancestor of" in f for f in failures), failures


def test_a_late_strip_cannot_green_a_predating_log(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-11 P1): the predating arm was the last one with no topology bound.

    A later commit stripping the numbers out of a predating log flipped the guard's central HARD
    FAIL into green ("exempt by CONTENT, measured nothing") — the content read was no longer the
    content that entered with the tree.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    # The strip: the same path, rewritten so any content reader would now see "no measurement".
    log.write_text("x\nstripped\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "strip the numbers", epoch=1_700_000_200)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    failures = _armed(prereg, [log])
    assert failures and "is not an ancestor of" in failures[0], failures


def test_a_pins_exempt_atomic_log_does_not_claim_its_disclosure(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-11 P2): the pass is the EXEMPTION's, so the disclosure is not its
    ground — deleting the disclosure from the identical tree yields the same verdict.

    ⛔ ARMED, AND ACTUALLY ATOMIC (#10546 review cycle 23, ISSUE 1): this test was vacuous twice over.
    It called `check_ordering` with no flag, so it ran the RULED path — where `atomic_added` is not a
    writable container at all, making `with_disclosure == []` unfalsifiable — over a fixture built by
    `_write_log` + `_write_and_commit`, which is TWO commits with an equal epoch: the pair was never
    atomic, so the arm this test names never ran. Its pins also named `e1-e2.log` while the log was
    `capstone.log`, and the call's return value was never asserted, so a stale-exemption finding went
    unseen. All four are fixed here.

    The mutation that settles the non-vacuity: add `atomic_added.append(log.name)` beside
    `atomic_seen.add(log.name)` in `_check_ordering_proposed`'s pins-exempt arm and THIS test reds;
    before this fix no test in the file did.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "capstone.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("x\n")
    log.write_text(UNSCORED_LOG)
    _git(repo, "add", ".")
    # ONE commit — the ATOMIC shape, which two commits at the same epoch are not.
    _git(repo, "commit", "-q", "-m", "both at once", epoch=1_700_000_000)
    pins = _exempt("capstone.log")
    with_disclosure: list[str] = []
    assert _armed(prereg, [log], pins, None, with_disclosure,
                  _atomic_pins(log.name), {}) == []
    assert with_disclosure == [], with_disclosure
    # The IDENTICAL tree minus the disclosure gives the same verdict — the pass is the pins
    # exemption's, so reporting a declaration here would name a ground that carried nothing.
    without: list[str] = []
    assert _armed(prereg, [log], pins, None, without, None, {}) == []
    assert without == [], without


def test_a_merge_landing_the_pair_unchanged_is_not_a_late_edit(tmp_path, monkeypatch):
    """REGRESSION (#10529 cycle-12 P1): the fleet's own rail lands with `--merge`, not `--squash`.

    `--full-history` lists the landing merge, and that merge is a DESCENDANT of the registration's
    add-commit — so a commit-PRESENCE bound reported it as a "late edit", consulted it BEFORE the
    disclosure was read, and left an atomic pair landed with `--no-ff` PERMANENTLY RED with no waiver
    route. The merge changed nothing; the bound is CONTENT now.
    """
    repo = _repo(tmp_path, monkeypatch)
    # `main` needs a commit of its own, so the merge is not a fast-forward.
    (repo / "README").write_text("root\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "root", epoch=1_700_000_000)
    # A feature branch adds both files in ONE commit, then main merges it --no-ff.
    _git(repo, "checkout", "-q", "-b", "feat")
    prereg, log = _both_in_one_commit(repo, epoch=1_700_000_000)
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "-m", "land the pair", "feat", epoch=1_700_000_500)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert failures == [], failures


def _both_in_one_commit(repo: Path, epoch: int = 1_700_000_000) -> tuple[Path, Path]:
    """The ATOMIC shape: the registration and the log land in the SAME commit.

    This is what a squash merge produces — and it is also what one commit written after the numbers
    were read produces. The git objects are identical, which is why the arm cannot compare them
    (#10529).
    """
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("x\n")
    log.write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "both at once", epoch=epoch)
    return prereg, log


def test_an_undisclosed_atomic_add_fails(tmp_path, monkeypatch):
    """Same commit + no disclosure is a FAILURE on BOTH paths, and each names its own cause.

    ⛔ THE DEFAULT IS THE RULED REFUSAL (#10546 review cycle 17, IS-1/IS-2). The earlier revision of
    this PR replaced the pre-fix message ("not strictly after") with "the SAME commit that added …",
    which quietly retired the sentence the recorded ruling keeps verbatim. Here the unarmed call
    keeps that refusal, and only the ARMED call — the proposal — offers the disclosure as a route.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    # DEFAULT: the ruling's verdict, with the ruled sentence still in it.
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1, failures
    assert "not strictly after" in failures[0], failures
    assert "6564634454" in failures[0], failures
    # ARMED (the proposal): a failure that names the atomic cause and the disclosure route.
    failures = _armed(prereg, [log])
    assert len(failures) == 1, failures
    assert "the SAME commit that added" in failures[0], failures
    assert check.ATOMIC_ADD_KEY in failures[0], failures


def test_the_ruled_path_never_instructs_a_disclosure_it_would_refuse(tmp_path, monkeypatch):
    """REGRESSION (#10546 review cycle 23, ISSUE 2): the default gate's ONE instruction must WORK.

    The ruled path appended "Disclose it under <record>.atomic_add_ordering or the ordering is
    unproven" — and, a few lines later on the SAME path, "it waives nothing unless the caller arms
    the proposed arm (--allow-atomic-disclosure)". The one concrete repair the default gate named was
    therefore unsatisfiable, and an author who obeyed it committed an INERT waiver into
    `preregistration.json`, which is the stale-waiver shape the anti-rot rule exists to refuse.

    Both halves are asserted, so a later lane cannot "restore" the sentence by tidying it back in:
    the ruled path offers ONLY the satisfiable repair (`SQUASH_COLLAPSE_REMEDY`), and the disclosure
    route survives on the arm that can honour it. Non-vacuous against the pre-fix tool, which printed
    the unsatisfiable sentence on this exact tree.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1, failures
    assert "Disclose it under" not in failures[0], failures
    # ... and the instruction it DOES print is the one that clears this refusal.
    assert "WITHDRAWAL" in failures[0] and "NEW artefact at a NEW path" in failures[0], failures
    # The disclosure route belongs to the ARMED path, where obeying it changes the verdict.
    armed = _armed(prereg, [log])
    assert len(armed) == 1, armed
    assert "Disclose it under" in armed[0], armed
    assert _armed(prereg, [log], None, None, None, _atomic_pins(log.name)) == []


def test_the_ruled_rule_states_its_claim_in_the_function_docstring():
    """REGRESSION (#10546 review cycle 23, minor): the claim must be IN `__doc__`, not after it.

    `_check_ordering_ruled` carried TWO adjacent string literals: the second — the module's own
    statement of the claim, ending "… and it must never report success." — was a dead expression
    statement, so `__doc__` was only the first text and every `help()`/pydoc reader lost the claim
    that the strict-epoch rule is measured against. Merged into one docstring; this pins it.
    """
    doc = check._check_ordering_ruled.__doc__ or ""
    assert "THE RULED VERDICT" in doc, doc
    assert "``[]`` when every log is either after the registration ON THE BASE" in doc, doc
    assert "must never report success" in doc, doc


def test_the_atomic_disclosure_arm_is_opt_in_and_the_ruled_verdict_is_the_default(
        tmp_path, monkeypatch):
    """NON-VACUITY of the disarm (#10546 review cycle 17, IS-1): one tree, two verdicts, one flag.

    This is the finding's whole substance made executable. The SAME tree carrying a COMPLETE, valid,
    resolvable disclosure — the best case the proposal can ever have — is RED without the flag and
    GREEN with it. If a later lane arms the arm by default, the first assertion goes green-with-a-
    notice and this test fails, which is exactly the review the ruling asks for.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    pins = _atomic_pins(log.name)
    disarmed = check.check_ordering(prereg, [log], None, None, None, pins)
    assert disarmed, "the ruled verdict must be a refusal even with a complete disclosure"
    assert "not strictly after" in disarmed[0], disarmed
    assert "waives nothing unless the caller arms" in disarmed[0], disarmed
    assert _armed(prereg, [log], None, None, None, pins) == []


def test_a_disclosed_atomic_add_passes_and_is_reported(tmp_path, monkeypatch):
    """ARMED: the disclosure turns an unprovable pair into a DECLARED one — never a silent pass."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    added: list[str] = []
    failures = _armed(prereg, [log], None, None, added, _atomic_pins(log.name))
    assert failures == [], failures
    assert added == [log.name]


def test_the_three_argument_form_agrees_with_the_reported_one(tmp_path, monkeypatch):
    """REGRESSION (#10529 review P1): the verdict must not depend on an OUT-PARAM.

    Deferring the anti-rot rule to `atomic_added` made the same tree and pins green through `run()`
    and red through the three-argument form every test and any future caller uses. An out-param
    reports; it may not judge.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    pins = _atomic_pins(log.name)
    assert _armed(prereg, [log], None, None, None, pins) == []
    assert _armed(prereg, [log], None, None, [], pins) == []


def test_an_atomic_and_unscored_log_may_be_disclosed(tmp_path, monkeypatch):
    """A log that is BOTH atomic and provably unscored is honestly disclosable (#10529 review P1)."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    assert _armed(prereg, [log], None, None, None, _atomic_pins(log.name)) == []


def test_an_atomic_and_unscored_log_is_exempted_on_the_ruled_path_too(tmp_path, monkeypatch):
    """The CONTENT exemption runs before the atomic refusal on BOTH rules (#10546 review, P3).

    ``_check_ordering_proposed`` now asserts this in-place (its atomic arm re-asks
    ``_log_declares_no_measurement`` instead of inheriting ``why`` from another arm's guard), and an
    assertion is only a claim until something measures the tree it describes. This is that tree: the
    pair is ATOMIC — the registration and the log land in ONE commit — and the log PROVES it measured
    nothing (the bundle's own documented re-run: ``run.status`` not COMPLETED, every number null), so
    it must be exempted by its CONTENT rather than refused for want of a disclosure. Measured with
    real log content, not a stubbed predicate: `_capstone_body(prereg_status="OK")`.

    It guards the arm ORDER on the ruled path too, which has no such assert: swap the atomic refusal
    ahead of the content exemption and this tree goes red on a log that measured nothing — the
    over-block #9089 review cycle 4 closed.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "capstone.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("x\n")
    log.write_text(_capstone_body(prereg_status="OK"))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "both at once", epoch=1_700_000_000)

    ruling = check.check_ordering(prereg, [log])
    assert ruling == [], ruling
    assert _armed(prereg, [log]) == []


def test_a_typed_shrug_is_not_a_disclosure(tmp_path, monkeypatch):
    """REGRESSION (#10529 review P2): `str({})` is truthy, so a truthiness test let a shrug pass."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    bad = [
        {"reason": {"a": 1}, "branch_commits": ["a1075dfc2 x"], "squash_merge": "cfa3de857 x"},
        {"reason": "because", "branch_commits": [{}], "squash_merge": "cfa3de857 x"},
        {"reason": "because", "branch_commits": ["a1075dfc2 x"], "squash_merge": "not a sha"},
        # A commit entry that names no commit sha points at nothing a reader can resolve.
        {"reason": "because", "branch_commits": ["some commit message"], "squash_merge": "cfa3de857 x"},
    ]
    for entry in bad:
        failures = _armed(prereg, [log], None, None, None, {log.name: entry})
        # EXACTLY one failure, and it is the field diagnosis — never the FALSE "not one" verdict
        # beside it (the log IS atomic; only its disclosure is malformed). cycle-3 P2.
        assert len(failures) == 1, (entry, failures)
        assert "not a usable disclosure" in failures[0], failures
        assert "not one" not in failures[0], failures
    # The SAME entries are refused when the log is atomic AND provably unscored (cycle-2 P2).
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    for entry in bad:
        assert _armed(prereg, [log], None, None, None, {log.name: entry}), entry


def test_a_squash_merge_no_reader_can_resolve_is_a_refusal(tmp_path, monkeypatch):
    """REGRESSION (#10529 review cycle 14, P2): `squash_merge` is required BECAUSE it resolves.

    The field's whole justification is "a squash leaves the branch commits unreachable from main,
    so the squash commit is the one pointer a reader can actually resolve". Checking only its
    FORMAT made that a decoration: `deadbeef1` passed every arm, and the CLI printed it as
    `squashed by deadbeef1` — the one line this change added to make the disclosure checkable,
    presenting an address that names nothing. Non-vacuous against the pre-fix tool, which accepted
    any 7-40 hex token here.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    pins = _atomic_pins(log.name, squash_merge="deadbeef1 measure: a merge that never landed")
    failures = _armed(prereg, [log], None, None, None, pins)
    assert len(failures) == 1, failures
    assert "not a usable disclosure" in failures[0], failures
    assert "squash_merge" in failures[0] and "does not have" in failures[0], failures
    # The SAME disclosure is refused when the log is atomic AND provably unscored: the field rule
    # belongs to the disclosure, not to whichever arm happens to run first (cycle-2 P2).
    monkeypatch.setattr(check, "_log_declares_no_measurement", lambda _p: (True, ""))
    assert _armed(prereg, [log], None, None, None, pins) == failures


def test_branch_commits_need_not_resolve_because_a_squash_hides_them(tmp_path, monkeypatch):
    """The OTHER half of the asymmetry (#10529 review cycle 14, P2) — do not "fix" it into a trap.

    A squash leaves the branch commits unreachable from `main`, so requiring the OBJECT here would
    make the disclosure permanently red in exactly the case it exists for — the stuck light this
    change removes. Format-only is deliberate; this test is what stops a later lane tidying the two
    fields into one rule and re-creating the outage.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    pins = _atomic_pins(
        log.name, branch_commits=["deadbeef1 the branch commit a squash made unreachable"]
    )
    assert _armed(prereg, [log], None, None, None, pins) == []


def _rev(repo: Path, ref: str) -> str:
    return subprocess.run(
        ["git", "rev-parse", ref], cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()


def _real_squash(repo: Path, epoch: int = 1_700_000_000) -> tuple[Path, Path, str, list[str]]:
    """A FAITHFUL squash: two branch commits, then ONE commit on main carrying both files.

    `_both_in_one_commit` reproduces the git OBJECT a squash leaves behind, but not the history it
    replaced — in a one-commit repo the "branch commits" a disclosure names cannot exist at all,
    so the field could only ever be exercised in its unreachable form. This builds the real shape
    (the bars frozen in one commit, the log added in a later one, both collapsed onto main), so the
    disclosure's claim is one the tool CAN verify and a test can make it fail on.
    """
    (repo / "README").write_text("root\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "root", epoch=epoch)
    base = _rev(repo, "HEAD")
    _git(repo, "checkout", "-q", "-b", "feat")
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "e1-e2.log"
    prereg.parent.mkdir(parents=True, exist_ok=True)
    prereg.write_text("x\n")
    _git(repo, "add", str(prereg.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "freeze the bars", epoch=epoch)
    log.write_text("x\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "add the log", epoch=epoch + 1)
    branch = [line for line in subprocess.run(
        ["git", "rev-list", "--reverse", "feat", "--not", base],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.split() if line]
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--squash", "feat")
    _git(repo, "commit", "-q", "-m", "both at once", epoch=epoch + 2)
    return prereg, log, _rev(repo, "HEAD"), branch


def test_a_real_squash_disclosure_is_verified_and_a_forged_squash_is_refused(tmp_path, monkeypatch):
    """The disclosure's claim is CHECKED when the history it names is still in the repo.

    REGRESSION (#10529 review cycle 15, P1). The passing fixture used to cite HEAD as BOTH its
    branch commit and its squash — a commit citing itself as its own provenance, which is the shape
    a single laundering commit has. Here the honest disclosure (two real branch commits, the real
    squash commit that collapsed them) passes, and a disclosure that names a commit which PREDATES
    the pair as its squash is refused: that pointer resolves, so the addressability rule alone could
    not tell it from an honest one. Non-vacuous — the pre-fix tool accepted both.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log, squash, branch = _real_squash(repo)
    assert len(branch) == 2, branch
    honest = _atomic_pins(
        log.name,
        branch_commits=[f"{sha[:9]} a branch commit the squash collapsed" for sha in branch],
        squash_merge=f"{squash[:9]} the merged commit",
    )
    assert _armed(prereg, [log], None, None, None, honest) == []
    # The commits it names are really here, so the CLI has nothing to disclaim.
    assert check._unverified_history_note(honest[log.name]) == ""

    forged = _atomic_pins(
        log.name,
        branch_commits=[f"{sha[:9]} a branch commit the squash collapsed" for sha in branch],
        squash_merge=f"{_rev(repo, 'HEAD~1')[:9]} the commit main sat at BEFORE the landing",
    )
    failures = _armed(prereg, [log], None, None, None, forged)
    assert len(failures) == 1, failures
    assert "not a usable disclosure" in failures[0], failures
    assert "nor a descendant of it" in failures[0], failures


def test_a_squash_merge_cannot_be_one_of_its_own_branch_commits(tmp_path, monkeypatch):
    """REGRESSION (#10529 review cycle 15, P1): a commit cannot collapse ITSELF.

    The two halves of a disclosure assert different things — `branch_commits` is where the ordering
    DID exist, `squash_merge` is the new commit that collapsed them — so naming one commit in both
    is self-referential, and it is exactly what one laundering commit writes. Non-vacuous: the
    pre-fix tool accepted this pair and passed it as a disclosed atomic add; it was the DEFAULT of
    its own passing fixture.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    head = _rev(repo, "HEAD")
    pins = _atomic_pins(log.name, branch_commits=[f"{head[:9]} the branch commit it collapsed"])
    failures = _armed(prereg, [log], None, None, None, pins)
    assert len(failures) == 1, failures
    assert "not a usable disclosure" in failures[0], failures
    assert "ALSO one of its branch_commits" in failures[0], failures
    # A DIFFERENT, aborted-length spelling of the same commit is the same commit (#10529 c15 P1).
    assert check._sha_prefix_agrees("cfa3de857", "cfa3de857ef2c") is True
    assert check._sha_prefix_agrees("cfa3de857", "cfa3de858") is False


def test_a_squash_merge_that_predates_the_pair_is_refused(tmp_path, monkeypatch):
    """REGRESSION (#10529 review cycle 15, P1): the squash pointer must be able to carry the pair.

    `squash_merge` is printed as the pointer ON MAIN a reader resolves, so an ancestor of the pair's
    add-commit — the commit main sat at BEFORE the landing — describes a history in which the pair
    is not in the tree. It resolves, so format and addressability both pass it; only ancestry can
    tell. Non-vacuous: the pre-fix tool accepted this.
    """
    repo = _repo(tmp_path, monkeypatch)
    (repo / "README").write_text("root\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "root", epoch=1_700_000_000)
    root = _rev(repo, "HEAD")
    prereg, log = _both_in_one_commit(repo, epoch=1_700_000_100)
    pins = _atomic_pins(log.name, squash_merge=f"{root[:9]} the commit before the landing")
    failures = _armed(prereg, [log], None, None, None, pins)
    assert len(failures) == 1, failures
    assert "nor a descendant of it" in failures[0], failures


def test_the_cli_says_when_a_disclosure_history_was_not_checked_here():
    """A claim this tool could not check may not read as one it did (#10529 review cycle 15, P1).

    `branch_commits` may legitimately not resolve — a squash leaves them unreachable from main —
    so their absence is not a failure. Silence was: the block printed the commits and nothing said
    none of them had been read. Where at least one DOES resolve the check ran, so there is nothing
    to disclaim.
    """
    absent = check._unverified_history_note(
        {"squash_merge": "cfa3de857 x", "branch_commits": ["deadbeef1 y"]}
    )
    assert "NOT checked here" in absent, absent
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(check.REPO_ROOT),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert check._unverified_history_note(
        {"squash_merge": "cfa3de857 x", "branch_commits": [f"{head[:9]} y"]}
    ) == ""
    assert check._unverified_history_note({"squash_merge": "x", "branch_commits": []}) == ""


def test_the_cli_prints_the_declared_atomic_add_and_its_commits(monkeypatch, capsys):
    """A declared atomic add must reach the human path, and name the commits it rests on.

    ⛔ NO LONGER DRIVEN BY THE 5087 BUNDLE (#10544). That bundle's log was WITHDRAWN on `main`
    (`108cec779`, ruling item 3) before this PR could land, so the directory it is checked against
    now holds ZERO evidence logs and the atomic arm cannot fire there — see
    `test_the_withdrawn_bundle_no_longer_carries_an_atomic_disclosure`. Printing the block is
    therefore exercised on a report built here: the human path is what this test is about, and it
    must not depend on a bundle's history to be covered.

    ⛔ AND IT MUST NOT DEPEND ON FULL HISTORY EITHER (#10546 review cycle 16, IS-2). The fixture's
    `branch_commits` used to be the REAL bundle's shas (`a1075dfc2`, `325f2a1a5`), and the
    "nothing to disclaim" half then asserted `"NOT checked here" not in out`. Those commits exist
    only in a FULL clone: the `test` shards check out at `fetch-depth: 1`
    (`.github/workflows/python-ci.yml` — the only `fetch-depth: 0` checkout is `changes`), so
    `_unverified_history_note` printed the disclaimer and the assertion failed on the shard that
    runs this file. `main()` never consults `history_unavailable()` and `check.run` is
    monkeypatched here, so nothing short-circuited it — the test would have red-ed `python-ci-gate`
    in the very run that landed it. It now names HEAD, which exists in EVERY clone however shallow,
    so the "resolves, so there is nothing to disclaim" half is depth-1-safe by construction — no
    `@requires_history` skip, which would have dropped this coverage from CI entirely.
    """
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(check.REPO_ROOT),
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    report = {
        "document": "docs/evidence/7949-capstone/PRE-REGISTRATION.md",
        "rows": [{"id": "E1", "line": 1}],
        "evidence_logs": ["e1-e2.log"],
        "ok": True,
        "failures": [],
        "ordering": "verified (atomic-add DECLARED: e1-e2.log)",
        "run_order_claim": "verified (atomic-add DECLARED: e1-e2.log)",
        "ordering_exempt": [],
        "ordering_atomic_add": ["e1-e2.log"],
        "ordering_atomic_add_commits": {
            "e1-e2.log": {
                "squash_merge": "cfa3de857 measure(10463): … ratio 0.870 (#10468)",
                "branch_commits": [f"{head[:9]} prereg(10463): the bars do not move"],
            }
        },
    }
    monkeypatch.setattr(check, "run", lambda *a, **k: report)
    assert check.main(["--dir", "docs/evidence/7949-capstone"]) == 0
    out = capsys.readouterr().out
    assert "ATOMIC-ADD declared" in out, out
    assert "squashed by cfa3de857" in out, out
    assert head[:9] in out, out
    # The CLAIM line is qualified — a consumer of `machine record run-order claim` must not read a
    # disclosed ordering as one the gate compared.
    assert "verified (atomic-add DECLARED" in out, out
    # This commit DOES exist in this checkout — it is HEAD, so it resolves in a depth-1 clone too —
    # so the block has nothing to disclaim.
    assert "NOT checked here" not in out, out
    # …and when it does NOT, the block says so instead of letting silence read as verification
    # (#10529 review cycle 15, P1).
    report["ordering_atomic_add_commits"]["e1-e2.log"]["branch_commits"] = [
        "deadbeef1 the branch commit a squash left unreachable",
    ]
    assert check.main(["--dir", "docs/evidence/7949-capstone"]) == 0
    out = capsys.readouterr().out
    assert "deadbeef1" in out, out
    assert "NOT checked here" in out, out


def test_the_withdrawn_bundle_no_longer_carries_an_atomic_disclosure():
    """The withdrawn log stays outside the guard's glob, and nothing here needs a disclosure.

    ⛔ THIS IS THE `#10544` INTERACTION, PINNED (#10546 review cycle 14, P2 named it; cycle 15 saw
    it land). `main` took the ruling's item 3 — the log moved under `withdrawn/`, outside the
    guard's NON-RECURSIVE glob — so an `atomic_add_ordering` entry naming it would be a STALE
    WAIVER: the stale rule reports "discloses '…', which is not in this directory" and `changes`
    goes red again on landing. The disclosure was removed WITH the log; this test is what stops it
    being restored by someone re-reading this PR's earlier body, which described a bundle state
    that no longer exists.

    ⛔ RE-DERIVED at the merge of #10554 (cycle 19). The old form asserted the directory carried NO
    evidence log at all, which was true only while the withdrawal was its most recent event. `main`
    has since landed a DIFFERENT log there — `s3-ablation-a83c23a8.log`, the re-measurement taken
    with the registration read from the commit — so "no log" is not the invariant and pinning it
    would refuse `main`'s own work. The invariant this test exists for is narrower and unchanged:
    the WITHDRAWN log is outside the glob, every log that IS in it has a PROVEN order (so none of
    them needs a disclosure), and no disclosure is recorded or accepted as inert. Anything smuggled
    back in still reds on the ordering half, which is what the last two assertions read.

    ⛔ THE HISTORY-FREE HALF RUNS BEFORE THE SKIP (#10546 review cycle 20, IS-2). It used to sit
    entirely behind `history_unavailable()`, which is TRUE in every CI `test` job (fetch-depth 1) —
    so the pin this test is advertised as skipped in 100% of CI runs and no merge gate ever
    evaluated it. The glob membership, the `withdrawn/` placement and the record key need no git
    history at all, so they are asserted FIRST; only the ordering half (`check.run`) stays behind
    the skip. Cycle 16 applied the same standard to the sibling CLI test; this is that standard
    applied here.
    """
    directory = PREREG_DIR.parent / "5087-hypothesis"
    present = sorted(p.name for p in directory.glob(check.EVIDENCE_LOG_GLOB))
    assert "s3-ablation-a1075dfc.log" not in present, (
        "the WITHDRAWN log is back in the directory the ordering half reads — see #10544"
    )
    assert (directory / "withdrawn" / "s3-ablation-a1075dfc.log").is_file(), (
        "the withdrawn log must stay under withdrawn/, which the non-recursive glob does not read "
        "— deleting it is not the fix this test pins"
    )
    assert check._record_atomic_add(directory) == {}
    if check.history_unavailable():
        pytest.skip(
            "the ordering half needs full history (CI test jobs check out at fetch-depth:1) — "
            "the glob, placement and record assertions above already ran"
        )
    report = check.run(directory, use_corpus=False)
    assert report["ok"] is True, report["failures"]
    assert report["ordering_atomic_add"] == [], report["ordering_atomic_add"]
    assert "ordering_atomic_add_commits" not in report, report.get("ordering_atomic_add_commits")
    # A bare "verified" — the ordering half compared no log at all for this bundle.
    assert report["ordering"] == "verified"
    assert check._record_atomic_add(directory) == {}


def _documenting_block_above(name: str) -> str:
    """The contiguous `#:` block immediately above ``name = …`` in the tool's source, as one string.

    Read from the source rather than restated, so a claim in the comment and the code it describes
    cannot drift apart: a `#:` block is the ONLY definition an author reads.
    """
    lines = Path(check.__file__).read_text(encoding="utf-8").splitlines()
    at = next(n for n, line in enumerate(lines) if line.split("=")[0].strip() == name)
    block: list[str] = []
    for line in reversed(lines[:at]):
        if not line.startswith("#:"):
            break
        block.append(line)
    assert block, f"no `#:` block documents {name}"
    return "\n".join(reversed(block))


def test_the_atomic_add_record_key_documents_every_required_field():
    """The comment that DEFINES the disclosure schema may not prescribe the refused shape.

    ⛔ (#10546 review cycle 20, IS-1) `ATOMIC_ADD_KEY`'s `#:` block is the ONLY definition of the
    key an author reads, and it documented a TWO-field shape — `{log name: {reason,
    branch_commits}}` — while `_atomic_add_failure` requires a third, `squash_merge`, and refuses
    the two-field block as "not a usable disclosure" (pinned by
    `test_an_undisclosed_atomic_add_is_not_excused_by_an_empty_reason`). The omitted field is the
    one pointer a reader can RESOLVE: a squash leaves `branch_commits` unreachable from `main`, so
    `squash_merge` is the only commit the disclosure can be checked against.

    The contiguous `#:` block immediately above the assignment must name every required field. On
    the pre-fix code it names two, so this fails there.
    """
    documented = _documenting_block_above("ATOMIC_ADD_KEY")
    for field in ("reason", "branch_commits", "squash_merge"):
        assert field in documented, (
            f"{check.ATOMIC_ADD_KEY}'s only definition omits {field!r}, which the validator "
            f"REQUIRES — an author following it writes a block the tool refuses:\n{documented}"
        )


def test_the_atomic_add_record_key_does_not_say_the_block_goes_unread_without_the_arm():
    """The ONLY definition of the key must not tell an author the default path ignores the block.

    ⛔ (#10546 review cycle 29, ISSUE 2) The block stated *"The block is read only when
    ``--allow-atomic-disclosure`` is passed"*. That is FALSE on the path this PR ships, in three
    ways, each of them ADDED by this PR and each already pinned by a sibling test:

    * invalid/missing fields are refused while disarmed — `disclosure_failed.add(name)` at the top
      of `_check_ordering_ruled` (`test_an_undisclosed_atomic_add_is_not_excused_by_an_empty_reason`
      calls `check_ordering` with the arm OFF);
    * a name that is not in the directory is a stale-waiver failure on that same path;
    * the anti-rot loop runs while disarmed BY DESIGN, so an entry on an ordinarily ordered log
      reds the default gate (`test_a_disclosure_for_a_normally_ordered_log_fails_with_the_arm_disarmed`).

    Only "the pair is REFUSED and a present block is reported inert" is true, and only for the
    atomic case. An author who reads the old sentence concludes an entry is harmless without the
    flag — and committing one for an ordinarily ordered log reds the default gate. So the block
    must state what the arm actually buys (the PASS) and that the block is READ and can FAIL
    without it. On the pre-fix text neither is there, and the false clause IS, so this fails there.
    """
    flat = " ".join(_documenting_block_above("ATOMIC_ADD_KEY").replace("#:", " ").split())
    assert "read only when" not in flat, (
        f"{check.ATOMIC_ADD_KEY}'s only definition still says the block is read only when the arm "
        f"is passed — but the disarmed path validates fields, refuses a stale name and runs the "
        f"anti-rot rule on it:\n{flat}"
    )
    assert "NOT IGNORED WITHOUT IT" in flat, flat
    for disarmed_failure in ("invalid", "not in the directory", "anti-rot"):
        assert disarmed_failure in flat, (
            f"{check.ATOMIC_ADD_KEY}'s only definition must name the way the block can FAIL while "
            f"the arm is disarmed ({disarmed_failure!r} is missing):\n{flat}"
        )


def test_the_base_ref_env_constant_carries_the_doc_comment_written_for_it():
    """`BASE_REF_ENV`'s `#:` block must document the CONSTANT, not the function below it.

    ⛔ (#10546 review cycle 29, ISSUE 2/P3) `_touch_commits` was inserted BETWEEN the block and the
    assignment it documents, so the block — *"The ref the ordering is measured against …"* — read
    as the doc comment of a function that consults NO ref, and the constant it was written for sat
    89 lines further down with nothing above it but blank lines. A `#:` block is the only
    definition a reader gets, so a misplaced one documents the wrong object. On the pre-fix layout
    there is no `#:` block above the assignment at all, which is why this fails there.
    """
    documented = _documenting_block_above("BASE_REF_ENV")
    assert check.BASE_REF_ENV in documented, documented
    assert "--base-ref" in documented, documented
    assert "origin/main" in documented, documented


def test_an_undisclosed_atomic_add_is_not_excused_by_an_empty_reason(tmp_path, monkeypatch):
    """A shrug is not a disclosure — an empty `reason` or an empty `branch_commits` fails closed."""
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    for entry in ({"reason": "", "branch_commits": ["a1075dfc2 x"]},
                  {"reason": "because", "branch_commits": [], "squash_merge": "cfa3de857 x"},
                  {"reason": "because", "squash_merge": "cfa3de857 x"},
                  # The squash commit is REQUIRED, not merely format-checked (cycle-4 P2): a squash
                  # leaves the branch commits unreachable, so this is the only resolvable pointer.
                  {"reason": "because", "branch_commits": ["a1075dfc2 x"]},):
        failures = check.check_ordering(prereg, [log], None, None, None, {log.name: entry})
        assert failures and "not a usable disclosure" in failures[0], failures


def test_a_disclosure_for_a_normally_ordered_log_fails(tmp_path, monkeypatch):
    """A waiver granted where none is needed is how a waiver set becomes routine (#10529).

    The same anti-rot rule `pins.pre_existing_unscored_logs` applies: an atomic-add disclosure may
    only be carried by a log whose add-commit IS the registration's, so it cannot be pre-granted to
    a future log and quietly cover a real ordering failure.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_100)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert len(failures) == 1, failures
    assert "not one" in failures[0], failures


def test_a_disclosure_for_a_normally_ordered_log_fails_with_the_arm_disarmed(tmp_path, monkeypatch):
    """REGRESSION (#10546 review cycle 18, IS-1): the anti-rot rule must fire on the DEFAULT path.

    The rule used to be gated on `allow_atomic_disclosure`, so a disclosure naming an ORDINARILY
    ordered log was accepted SILENTLY in every invocation CI performs — the CLI, `run()` and the
    check step all leave the arm disarmed. The gate's stated reason (a false "but it is not one"
    for a genuinely atomic log) is already handled by `atomic_no_matter`, so the gate only served
    to enforce the rule on the one path nothing calls: a tree that is green today would red the day
    the arm is armed, which is the silent-green shape this rule exists to refuse.

    The disclosure here is COMPLETE and resolvable, so nothing but the anti-rot rule can red it —
    and the pre-fix default returned `[]`, which is why this test fails on that code.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_100)
    assert _armed(prereg, [log], None, None, None, _atomic_pins(log.name))  # the rule, armed
    # …and the DEFAULT, which is what CI runs: identical verdict, not a silent pass.
    failures = check.check_ordering(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert len(failures) == 1, failures
    assert "not one" in failures[0], failures


def test_an_atomic_logs_disclosure_is_not_called_stale_while_the_arm_is_disarmed(tmp_path, monkeypatch):
    """The anti-rot loop runs while disarmed, and it must not invent rot for a GENUINE atomic add.

    Companion to the test above: dropping the `allow_atomic_disclosure` gate puts an atomic log's
    own disclosure through the loop on the default path. `atomic_no_matter` is populated for every
    atomic log before any arm runs, so the disclosure is skipped there — while the log's REFUSAL
    (the ruled verdict) is still reported, with no false "but it is not one" beside it.
    """
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    failures = check.check_ordering(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert len(failures) == 1, failures
    assert "not one" not in failures[0], failures
    assert "SAME commit" in failures[0], failures


def test_a_disclosure_naming_an_absent_log_fails(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    prereg, log = _both_in_one_commit(repo)
    failures = check.check_ordering(prereg, [log], None, None, None, _atomic_pins("gone.log"))
    assert any("stale waiver" in f for f in failures), failures


def test_the_attack_is_still_undeclarable(tmp_path, monkeypatch):
    """REGRESSION: #10529 must NOT weaken the `log_epoch < prereg_epoch` arm.

    The log added first and the thresholds written afterwards is THE defect the guard exists for.
    A disclosure must not launder it, and the message must stay the original one.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = _armed(prereg, [log], None, None, None, _atomic_pins(log.name))
    assert any("is an ANCESTOR of the pre-registration's add-commit" in f for f in failures), failures
    assert any("not one" in f for f in failures), failures


def test_an_uncommitted_registration_cannot_claim_ordering(tmp_path, monkeypatch):
    repo = _repo(tmp_path, monkeypatch)
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    prereg.write_text("x\n")  # written, never committed
    failures = check.check_ordering(prereg, [log])
    assert failures and "not committed" in failures[0], failures


def test_an_untracked_log_does_not_fail_the_ordering(tmp_path, monkeypatch):
    """A log being authored right now cannot predate a commit that already exists."""
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    log = prereg.parent / "e5-falsifier.log"
    log.write_text("x\n")
    assert check.check_ordering(prereg, [log]) == []


# ── the bounded exemption: an unscored log that predates the registration ────

UNSCORED_LOG = (
    "sha=deadbeef\n"
    "prereg=UNPREREGISTERED artifact=docs/evidence/7949-capstone/preregistration.json\n"
    "e1 before_share=0.6 after_share=0.6 threshold=n/a verdict=UNPREREGISTERED\n"
    "result=UNPREREGISTERED\n"
    "exit=3\n"
)


def _write_log(repo: Path, name: str, epoch: int, body: str = UNSCORED_LOG) -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", f"add {name}", epoch=epoch)
    return target


def _exempt(name: str = "e1-e2.log") -> dict:
    return {"pre_existing_unscored_logs": {name: {"verdict": check.UNSCORED}}}


def test_a_pre_existing_unscored_log_is_exempt_from_the_ordering(tmp_path, monkeypatch):
    """THE FIX (#9075): history put the log first; the log scores nothing, so it is exempt.

    Without this, the check is a stuck light: `e1-e2.log` was added before any pre-registration
    could exist, so a bare date comparison can only ever report failure and #9075 could not land.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []


def test_an_exempted_log_that_scores_anything_voids_its_exemption(tmp_path, monkeypatch):
    """The laundering attempt: grandfather a log, then let it carry a verdict."""
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("verdict=UNPREREGISTERED", "verdict=PASS")
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "verdict=PASS" in failures[0], failures


def test_an_exempted_log_that_does_not_declare_itself_unscored_fails(tmp_path, monkeypatch):
    """The exemption reads the LOG, never the document — a document's word is not evidence."""
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("prereg", "something-else")
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "does not declare" in failures[0], failures


def test_an_exempted_log_without_exit_3_fails(tmp_path, monkeypatch):
    """An UNPREREGISTERED log must exit 3; a status and an outcome that disagree is a defect."""
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("exit=3", "exit=0")
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "must exit 3" in failures[0], failures


def test_an_exemption_for_a_log_that_postdates_the_registration_fails(tmp_path, monkeypatch):
    """A postdating log needs no exemption, so granting one hides a real ordering failure."""
    repo = _repo(tmp_path, monkeypatch)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_000)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "AFTER the pre-registration" in failures[0], failures


def test_an_exemption_naming_an_absent_log_fails(tmp_path, monkeypatch):
    """An exemption that exempts nothing is how an exempt set is grown one stale row at a time."""
    repo = _repo(tmp_path, monkeypatch)
    _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    pins = check.parse_pins(PREREG.read_text())
    pins["pre_existing_unscored_logs"]["ghost.log"] = {"verdict": check.UNSCORED}
    failures = check.check_ordering(
        prereg, sorted(prereg.parent.glob("*.log")), pins)
    assert any("not in this directory" in f for f in failures), failures


def test_two_scored_measurement_logs_are_refused(tmp_path):
    """One artifact claims to be THE measurement — a superseded log is REMOVED, not kept.

    `check_ordering` iterates every `*.log` it finds and never counts the SCORED ones, so a
    superseded run left beside the current one was accepted and the directory held two artifacts
    claiming to be the measurement. The refusal must be legible (this runs as a required CI step):
    it names both files and the repair.
    """
    (tmp_path / "capstone.log").write_text("the separate REFUSAL record\n")
    (tmp_path / "e1-e2.log").write_text("the arm log\n")
    assert check.check_single_measured_log(tmp_path) == []
    (tmp_path / "capstone-measured-aaaaaaa.log").write_text("scored\n")
    assert check.check_single_measured_log(tmp_path) == []
    (tmp_path / "capstone-measured-bbbbbbb.log").write_text("scored\n")
    failures = check.check_single_measured_log(tmp_path)
    assert len(failures) == 1, failures
    message = failures[0]
    for fragment in ("capstone-measured-aaaaaaa.log", "capstone-measured-bbbbbbb.log",
                     "exactly ONE", "RE-RUNNING", "DELETING"):
        assert fragment in message, (fragment, message)
    # the non-scored logs are never counted against the rule
    assert "capstone.log," not in message and "e1-e2.log" not in message, message


# ── the machine record must agree with the argument ──────────────────────────


def _record(**over) -> dict:
    pins = check.parse_pins(PREREG.read_text())
    rows = check.parse_criteria_rows(PREREG.read_text())
    base = {
        "schema": check.PREREG_SCHEMA,
        "written_before_any_scored_run": True,
        "written_before_any_scored_run_basis": f"no scored run exists; the {check.UNSCORED} log predates it",
        "unscored_runs_before_this_registration": sorted(
            pins["pre_existing_unscored_logs"]),
        "corpus": {k: pins["corpus"][k] for k in check.CROSS_CHECKED_CORPUS_KEYS},
        "denominator_floors": dict(pins["denominator_floors"]),
        "split": {"tune": list(pins["split"]["tune"]),
                  "held_out": list(pins["split"]["held_out"])},
        "e4_denominator": pins["e4_denominator"],
        # The record must pin the document it describes; `_materialise` copies the real document,
        # so the digest is the real one (#9075 review r3 P3).
        check.DOCUMENT_SHA_PIN: "sha256:" + hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "criteria": [{"id": r["id"], "threshold": r["threshold"]} for r in rows],
    }
    base.update(over)
    return base


def _materialise(tmp_path: Path, record) -> Path:
    (tmp_path / check.PREREG_NAME).write_text(PREREG.read_text())
    import json as _json
    (tmp_path / check.MACHINE_RECORD).write_text(_json.dumps(record))
    return tmp_path


def test_the_real_machine_record_agrees_with_the_pins():
    """The record pins its document AND the code lines it cites.

    Round-14 review: this PR added two comment lines inside `DOOR_PINNED_ON`,
    which moved the `TORTOISE_STATE_VALUE_VERBATIM` entry `:504` -> `:506`, and
    the record's `door.acceptance_arm.read_back` was left quoting `:504` - a
    comment line. The field's OWN prose warns that this entry "is NOT
    cite-neutral" for exactly this reason, and nothing reddened: the checker
    (`tools/capstone_preregistration_check.py`) never reads `read_back`, and
    `grep -rn ':506' tests/ tools/` was empty. A field that warns about cite
    drift cannot be the one place drift goes unwatched, so its
    `capstone_measure.py` cites are pinned here EXACTLY - the entry line, and the
    census range's own opening and closing lines - never as a window a shift can
    slide inside. Kept inside THIS test rather than added as a new test in
    `tests/test_7949_capstone_measure.py`, because the thing pinned is THIS
    file's own subject (`check.check_machine_record` over the machine record),
    and THIS file's durations row states no case count, so a pin here moves no
    shard-layout input. (Measured rather than assumed: `config/ci-surfaces.yml`
    has no `cases` key at all — its rows carry a WEIGHT and a prose comment — and
    this file's own row states no count.) ⛔ BUT THE OTHER ROW DOES, in that
    prose: `test_7949_capstone_measure.py`'s row states a case count, re-derived
    there from `grep -c '^def test_'`, so adding a `def test_` THERE would
    invalidate it. That is a SECOND and stronger reason to keep this pin here —
    not a reason it makes no difference either way, which is what an earlier cut
    of this sentence claimed.
    """
    pins = check.parse_pins(PREREG.read_text())
    assert check.check_machine_record(PREREG_DIR, pins) == []

    read_back = json.loads((PREREG_DIR / "preregistration.json").read_text())[
        "door"]["acceptance_arm"]["read_back"]
    probe = (PREREG_DIR / "probes" / "capstone_measure.py").read_text().splitlines()

    # ⛔ A NEGATIVE LOOKAHEAD, not containment: `":506" in read_back` is satisfied by
    # `:5060` — a line the 4,349-line probe does not have — so containment certified a
    # cite pointing at nothing (measured: re-staling the field to `:5060` left this row
    # GREEN until this regex was written).
    assert re.search(r"probes/capstone_measure\.py:506(?!\d)", read_back), (
        "the record must name the line the TORTOISE_STATE_VALUE_VERBATIM entry is on, "
        "as a complete number")
    assert probe[506 - 1].strip() == '("TORTOISE_STATE_VALUE_VERBATIM", "1"))', (
        f"capstone_measure.py:506 reads {probe[506 - 1].strip()!r}, not the pinned entry")

    # ⛔ A SET OVER THE RANGES CITED, not a count and not a phrase. The first cut of this
    # arm asserted `count(":2726-2728") == 2`, which false-failed a CORRECT additive edit
    # (one more accurate mention of the same range) — a false BLOCK, the class this bundle
    # forbids as much as a false pass — and the second cut pinned the two slots as full
    # sentences, which false-failed on reworded connectives ("which sits below" ->
    # "sitting below"). What the field actually claims is WHICH RANGES EXIST and where the
    # last move went, so the set below is pinned (a fabricated intermediate like `:2722-2724`
    # reds it) and the two slots are matched with tolerant connectives (a reword with every
    # number intact passes). Both relaxations were measured: the additive mention and the
    # `to` spelling each left the earlier cuts RED and this one GREEN, while a re-staled
    # copy still reds.
    cited_ranges = set(re.findall(r":(\d+-\d+)", read_back))
    _expected_ranges = {"275-283", "2713-2715", "2724-2726", "2726-2728"}
    assert cited_ranges == _expected_ranges, (
        "the record's cited ranges are "
        f"{sorted(cited_ranges)}; missing={sorted(_expected_ranges - cited_ranges)} "
        f"unexpected={sorted(cited_ranges - _expected_ranges)} — an unexpected range is a "
        "fabricated or re-staled position, a missing one is a claim that left the record")
    # ⛔ EACH MOVE PINNED BY ADJACENCY, and the live slot by COUNT rather than by its verb.
    # A set alone cannot tell the two mentions of `:2724-2726` apart (it is `a1dff7cec8`'s
    # move TARGET and this branch's move SOURCE), so `:2713-2715 -> :2724-2726` swapped to
    # `-> :2726-2728` stayed GREEN inside the set while crediting that commit with the final
    # position — the one thing the field's own last clause denies (measured). With BOTH
    # moves adjacency-pinned the duplicate can no longer shield a swap. And `carries` was
    # itself a prose pin (`cites` redded with every number intact), so the live slot is
    # `count >= 2`: the move supplies one mention and the live cite must supply the other,
    # so deleting or re-staling the live one leaves 1 and reds.
    assert re.search(r":2713-2715\s*(?:->|→|to)\s*:2724-2726\b", read_back), (
        "the record must attribute the FIRST move to a1dff7cec8, at the intermediate "
        "position it actually reached")
    assert read_back.count(":2726-2728") >= 2, (
        f"the record states :2726-2728 {read_back.count(':2726-2728')} time(s): the last "
        "move supplies one mention and the LIVE cite must supply the other")
    assert re.search(r":2724-2726\s*(?:->|→|to)\s*:2726-2728\b", read_back), (
        "the record must attribute the LAST move of that window to this branch, which "
        "is the half its own +2 comment lines are responsible for")
    # ⛔ No separate `":2713-2715" in read_back` arm: the SET pin above already requires
    # that literal, and it is evaluated first, so the smaller arm was UNREACHABLE — a
    # mutant that removed the window reddened the set row (`missing=['2713-2715']`) and
    # never reached it (measured, agent-infra#3442 rule 3: an assertion that can never be
    # the row that reds is not attribution, it is dead weight). The set pin's message
    # already names the missing range, so nothing is lost by deleting it.
    assert probe[2726 - 1].strip() == "for name, count, anchored in projection.g.query(", (
        f"capstone_measure.py:2726 reads {probe[2726 - 1].strip()!r}, not the census call")
    # Equality, like its two siblings above: a substring let the closing line's SHAPE
    # change while the row stayed green (measured: renaming the `AS anchored` alias broke
    # the very unpacking this arm consumes, and nothing reddened).
    assert probe[2728 - 1].strip() == '"count(r.sourceVersion) AS anchored ORDER BY t").result_set:', (
        f"capstone_measure.py:2728 reads {probe[2728 - 1].strip()!r}, not the "
        "anchored count that closes the query")


def test_a_missing_machine_record_fails(tmp_path):
    (tmp_path / check.PREREG_NAME).write_text(PREREG.read_text())
    failures = check.check_machine_record(tmp_path, check.parse_pins(PREREG.read_text()))
    assert failures and "no preregistration.json" in failures[0], failures


def test_a_machine_record_with_the_wrong_schema_fails(tmp_path):
    rec = _record(schema="something-else/v9")
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("declares schema" in f for f in failures), failures


def test_a_machine_record_threshold_that_disagrees_with_the_table_fails(tmp_path):
    """Two copies of a threshold that can disagree is how a gate reads one and a reviewer another."""
    rec = _record()
    rec["criteria"] = [dict(c) for c in rec["criteria"]]
    for crit in rec["criteria"]:
        if crit["id"] == "E1":
            crit["threshold"] = "`<= 0.90 x before_share`"
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("threshold for E1" in f for f in failures), failures


def test_a_machine_record_omitting_a_criterion_fails(tmp_path):
    rec = _record()
    rec["criteria"] = [c for c in rec["criteria"] if c["id"] != "E5"]
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("omits criteria" in f for f in failures), failures


def test_a_machine_record_whose_unscored_list_disagrees_with_the_pins_fails(tmp_path):
    rec = _record(unscored_runs_before_this_registration=[])
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("unscored_runs_before_this_registration" in f for f in failures), failures


def test_a_machine_record_without_the_scored_run_assumption_fails(tmp_path):
    rec = _record(written_before_any_scored_run=False)
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("written_before_any_scored_run" in f for f in failures), failures


def test_the_run_order_claim_must_state_its_basis(tmp_path):
    """An unqualified `true` is the claim #9075 exists to refuse; the basis names the unscored log."""
    rec = _record(written_before_any_scored_run_basis="because we say so")
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("without naming the basis" in f for f in failures), failures


def test_the_unqualified_written_before_any_run_field_is_refused(tmp_path):
    """REGRESSION (#9075 review P1): the name lies even when the prose is honest.

    `written_before_any_run: true` is false whenever ANY log exists — including an UNPREREGISTERED
    one — and a machine consumer keys on the NAME. The precise claim is
    `written_before_any_scored_run`, so carrying the ambiguous one is a failure even alongside it.
    """
    rec = _record(written_before_any_run=True)
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("unqualified written_before_any_run" in f for f in failures), failures


def test_the_real_machine_record_carries_only_the_precise_field():
    import json as _json
    record = _json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    assert record["written_before_any_scored_run"] is True
    assert "written_before_any_run" not in record

# ── the exemption cannot be laundered by rewriting the log in place ──────────


def test_an_exempted_log_edited_after_the_registration_fails(tmp_path, monkeypatch):
    """REGRESSION (#9075 review P1): `--diff-filter=A` cannot see an in-place edit.

    The add-commit is immutable, so a log can be re-committed with a fabricated measurement AFTER
    the thresholds were written and still look exempt. The exemption therefore bounds the log's
    CONTENT, not just its add-commit: the last commit touching it must predate the registration.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []

    # The forgery: keep every marker intact, only move the number — the exact edit that would be
    # read as a measurement against the frozen threshold.
    log.write_text(UNSCORED_LOG.replace("after_share=0.6", "after_share=0.25"))
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "rewrite the log", epoch=1_700_000_200)

    # THE RULED default: the last commit touching the log is not strictly before the
    # registration's appearance on the base.
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "not strictly before" in failures[0], failures
    # THE PROPOSAL (armed only): the same defect, on TOPOLOGY — the touching commit is not an
    # ancestor of the registration's add-commit, so a backdated date cannot hide it.
    failures = _armed(prereg, [log], _exempt())
    assert failures and "not an ancestor of" in failures[0], failures


@pytest.mark.parametrize("old,new,bad", [
    ("result=UNPREREGISTERED", "result=PASS", "result=PASS"),
    ("verdict=UNPREREGISTERED", "verdict=GREEN", "verdict=GREEN"),
])
def test_an_exempted_log_with_a_marker_other_than_unpreregistered_fails(
        tmp_path, monkeypatch, old, new, bad):
    """REGRESSION (#9075 review P2): the whitelist, not a four-string blacklist.

    `result=PASS` and `verdict=GREEN` were both admitted by the old blacklist, which only knew
    `verdict=PASS|FAIL` and `result=GREEN|RED` — the inverse of the gap the exemption closes.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=UNSCORED_LOG.replace(old, new))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and bad in failures[0], (bad, failures)


def test_an_unreadable_corpus_is_a_usage_error_not_a_traceback(tmp_path):
    """REGRESSION (#9075 review P2): a check that cannot read its subject must exit 2.

    This exercise is the EXPLICIT branch — a corpus root that is not there — while the wide
    `except (OSError, ImportError, …)` tuple `run()` wraps is covered separately by
    `test_every_unreadable_subject_becomes_a_check_error`. Both are exit 2; only the mechanism
    differs (#9075 review r5 note 4).
    """
    doc = _doc(tmp_path, _rows(
        "| E1 | shape Objects written | all Objects written (floor >= 30) | `<= 0.50 x before` |",
        "| E2a | value units surviving | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored derived nodes | all derived nodes (floor >= 30) | `= 1.000` |",
        "| E4 | wired+persisted operator edges | the **15** planted operator edges | `= 15 of 15` |",
        "| E5 | narrative-arm marker claims | raw-arm marker claims over **29** sentences | `>= 0.90` ratio |",
    ))
    pins = check.parse_pins(doc.read_text())
    pins["corpus"]["root"] = "tests/eval/write_path_DOES_NOT_EXIST"
    with pytest.raises(check.CheckError):
        check.derive_corpus_facts(pins)


def test_run_wraps_a_corpus_failure_as_a_check_error(tmp_path, monkeypatch):
    """`run()` must surface a mid-derivation IO failure as exit-2 material, not a traceback."""
    doc = _doc(tmp_path, _rows(
        "| E1 | shape Objects written | all Objects written (floor >= 30) | `<= 0.50 x before` |",
        "| E2a | value units surviving | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored derived nodes | all derived nodes (floor >= 30) | `= 1.000` |",
        "| E4 | wired+persisted operator edges | the **15** planted operator edges | `= 15 of 15` |",
        "| E5 | narrative-arm marker claims | raw-arm marker claims over **29** sentences | `>= 0.90` ratio |",
    ))
    assert doc.exists()

    def boom(_pins):
        raise FileNotFoundError("tests/eval/write_path/fixtures/wp01_quarry_debug.json")

    monkeypatch.setattr(check, "derive_corpus_facts", boom)
    with pytest.raises(check.CheckError) as excinfo:
        check.run(tmp_path, use_git=False, use_corpus=True)
    assert "cannot re-derive the corpus pins" in str(excinfo.value)
    assert "FileNotFoundError" in str(excinfo.value)


# ── the CLI contract ────────────────────────────────────────────────────────


@requires_history
def test_cli_exits_zero_on_the_real_registration(capsys):
    assert check.main(["--dir", str(PREREG_DIR)]) == 0
    out = capsys.readouterr().out
    assert "OK —" in out
    assert "operators=15" in out and "values=14" in out and "markers=29" in out


def test_cli_exits_two_when_the_document_is_absent(tmp_path, capsys):
    """A check that cannot read its subject must never report success."""
    assert check.main(["--dir", str(tmp_path)]) == 2
    assert "no PRE-REGISTRATION.md" in capsys.readouterr().err


def test_cli_exits_two_when_the_document_is_not_utf8(tmp_path, capsys):
    """#9089 review cycle 6: an undecodable document is a CHECK ERROR, not a finding.

    A non-UTF-8 `PRE-REGISTRATION.md` raised `UnicodeDecodeError`, which is not a
    `CheckError`, so it escaped `main`'s handler and surfaced as a traceback with exit 1 —
    the code this tool reserves for FINDINGS, on a document it never read. Exit 2 is the
    documented answer for an unreadable subject (`docs/capstone_verdict_check`'s sibling
    reader got the same hardening in this PR).
    """
    (tmp_path / check.PREREG_NAME).write_bytes(b"# pre-registration\n\xff\xfe not utf-8\n")
    assert check.main(["--dir", str(tmp_path), "--no-git", "--no-corpus"]) == 2
    assert "not valid UTF-8" in capsys.readouterr().err


def test_cli_exits_one_on_a_broken_document(tmp_path, capsys):
    target = _doc(tmp_path, _rows(
        "| E1 | shape Objects written |  | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    assert target.exists()
    assert check.main(["--dir", str(tmp_path), "--no-git", "--no-corpus"]) == 1
    assert "FAIL —" in capsys.readouterr().out


# ── #10536: the refusal must be COMPARABLE, not just honest ──────────────────
#
# The merge rail's exemption compares failures by IDENTITY. `FAIL — N problem(s):` is
# prose, so a step printing only it yields no identity, and the rail then refused to
# merge ANY PR while this bundle was red on `main` (measured: `ids=0` on run
# 41353799666, 14 PRs BLOCKED). These pin the annotation that closes that gap — and
# pin that it is an attribution only, never a verdict change.


def test_every_finding_is_also_emitted_as_a_rail_parseable_annotation(tmp_path, capsys):
    """One `::error::` per finding, carrying the document and the finding."""
    target = _doc(tmp_path, _rows(
        "| E1 | shape Objects written |  | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    assert target.exists()
    assert check.main(["--dir", str(tmp_path), "--no-git", "--no-corpus"]) == 1
    captured = capsys.readouterr()
    annotations = [ln for ln in captured.err.splitlines() if ln.startswith("::error::")]
    findings = [ln for ln in captured.out.splitlines() if ln.startswith("  - ")]
    assert findings, "the fixture must produce at least one finding"
    assert len(annotations) == len(findings), (annotations, findings)
    # `strict=True` because the length equality is already asserted on the line above;
    # B905 wants it explicit, and it turns a future drift into a failure here rather
    # than a silently truncated pairing.
    for annotation, finding in zip(annotations, findings, strict=True):
        assert check.PREREG_NAME in annotation
        assert finding.strip().removeprefix("- ").split(" ")[0] in annotation


def test_the_annotation_never_changes_the_verdict(tmp_path, capsys):
    """Attribution only: same exit code, same findings, same refusal.

    A gate that started passing because it became legible would be a fail-open of the
    exact shape this tool exists to prevent (#10529's rule is untouched).
    """
    target = _doc(tmp_path, _rows(
        "| E1 | shape Objects written |  | `<= 0.50 × before` |",
        "| E2 | value units | the **14** value units | `>= 13 of 14` |",
        "| E3 | anchored nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    ))
    assert target.exists()
    assert check.main(["--dir", str(tmp_path), "--no-git", "--no-corpus"]) == 1
    out = capsys.readouterr().out
    assert "FAIL —" in out
    # The human verdict line is unchanged — the annotation is additive.
    assert "OK" not in out


def test_the_annotation_is_one_line_and_raises_no_workflow_escape(tmp_path):
    """GitHub takes only the first line, and `%` opens an escape sequence."""
    line = check._rail_identity_annotation(
        "docs/evidence/x/PRE-REGISTRATION.md",
        "a.log: added at 1 which is not strictly after 2 — 50% of it\nsecond line\ttabbed",
    )
    assert line.startswith("::error::")
    assert "\n" not in line and "\t" not in line
    # Every `%` must open an escape — a bare one is invalid in a workflow command.
    assert "%25" in line
    assert re.search(r"%(?![0-9A-Fa-f]{2})", line) is None, line


def test_the_annotation_identity_discriminates_distinct_findings():
    """A NEW finding must not inherit an existing red's identity.

    The rail exempts a PR failure only when the SAME identity is red on `main`, and it
    NORMALISES the annotation before keying on it (`normalize_signature` -> `<HEX>`, then
    `_GUARD_INT_RE` -> `<N>`). "Different-looking strings" is therefore NOT the bar: the
    cases below are the ones that actually collided before :func:`_finding_discriminator`
    was added (review cycle 1 — this test previously passed while the guarantee was false).
    """
    doc = "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md"
    base = "s3-ablation-a1075dfc.log: added at 1793558646 which is not strictly after 2"
    first = check._rail_identity_annotation(doc, base)
    same = check._rail_identity_annotation(doc, base)
    # 1. A log name whose hex suffix the rail masks to `<HEX>`.
    other_log = check._rail_identity_annotation(
        doc, "s3-ablation-77d0be11.log: added at 1793999999 which is not strictly after 2")
    # 2. A digit-only bundle rename, which normalises to `<N>`.
    other_doc = check._rail_identity_annotation(
        "docs/evidence/9999-hypothesis/PRE-REGISTRATION.md", base)
    # 3. A wholly unrelated finding.
    unrelated = check._rail_identity_annotation(doc, "b.log: tracked but with no add-commit")

    assert first == same, "the same finding must key identically on both surfaces"
    for other in (other_log, other_doc, unrelated):
        assert first != other, (first, other)


def test_an_ordering_failure_emits_its_annotation_through_the_cli(tmp_path, monkeypatch, capsys):
    """The END-TO-END path that actually reds main — the ORDERING arm, not the document one.

    Every other annotation fixture here is a `--no-git` document failure, so the arm that
    produces the real red was untested. The finding text below is the verbatim shape
    `check_ordering` returns on the #10468 bundle (registration and log in ONE commit), and
    it is pushed through `main()` so the annotation channel, the escaping and the
    discriminator are all exercised on THAT text rather than on a synthetic string.
    """
    failure = ("s3-ablation-a1075dfc.log: added at 1793558646 which is not strictly after "
               "the pre-registration's add-commit 1793558646 — the log may have been scored "
               "against thresholds chosen after it was read, and it does not prove it "
               "measured nothing (carries 0 BEGIN and 0 END verdict markers — ambiguous)")
    report = {
        "document": "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md",
        "rows": [], "evidence_logs": ["s3-ablation-a1075dfc.log"],
        "ordering": "violated", "corpus": "skipped",
        "failures": [failure], "ok": False, "run_order_claim": "violated",
    }
    monkeypatch.setattr(check, "run", lambda *a, **k: report)

    assert check.main(["--dir", str(tmp_path), "--no-git", "--no-corpus"]) == 1
    captured = capsys.readouterr()
    annotations = [ln for ln in captured.err.splitlines() if ln.startswith("::error::")]
    assert len(annotations) == 1, annotations
    assert "not strictly after" in annotations[0]
    assert check._finding_discriminator(report["document"], failure) in annotations[0]


def test_the_discriminator_survives_the_rails_own_normalisation():
    """The discriminator must not be erasable by the masks the rail applies.

    `normalize_guard_error` lowercases nothing but masks `[0-9a-f]{8,}` as `<HEX>` and
    every `\\b\\d+\\b` as `<N>`. A discriminator built from lowercase hex or bare digits
    would be eaten, silently restoring the collision it exists to prevent — so it is
    asserted against those two shapes here, not assumed.
    """
    doc = "docs/evidence/5087-hypothesis/PRE-REGISTRATION.md"
    a = check._finding_discriminator(doc, "s3-ablation-a1075dfc.log: added at 1 after 2")
    b = check._finding_discriminator(doc, "s3-ablation-77d0be11.log: added at 9 after 2")
    assert a != b
    for token in (a, b):
        assert token.startswith("finding-")
        assert re.fullmatch(r"finding-[A-Z2-7]+", token), token
        # …and neither mask can touch it:
        assert re.search(r"[0-9a-f]{8,}", token) is None, token


@requires_history
def test_the_json_report_is_machine_readable(capsys):
    assert check.main(["--dir", str(PREREG_DIR), "--json"]) == 0
    import json
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["derived"]["planted_operator_edges"] == 15


@pytest.mark.parametrize("cell", ["", "TBD", "to be decided", "—", "-", "N/A"])
def test_every_placeholder_spelling_is_rejected(cell):
    assert check.is_placeholder(cell)


@pytest.mark.parametrize("cell", ["`>= 13 of 14`", "0.05", "`= 1.000`"])
def test_real_values_are_not_mistaken_for_placeholders(cell):
    assert not check.is_placeholder(cell)


# ── round-2 review regressions ───────────────────────────────────────────────


@pytest.mark.parametrize("old,new,bad", [
    ("verdict=UNPREREGISTERED", "verdict = PASS", "verdict=PASS"),
    ("result=UNPREREGISTERED", "result = GREEN", "result=GREEN"),
])
def test_a_spaced_scored_assignment_also_voids_the_exemption(
        tmp_path, monkeypatch, old, new, bad):
    """REGRESSION (#9075 review P2-2): the regex required a literal `=` with NO spaces.

    `verdict = PASS` was a false negative — invisible to the whitelist — which is the same
    "next scored vocabulary slips through with no code change" gap the whitelist closed.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=UNSCORED_LOG.replace(old, new))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and bad in failures[0], (bad, failures)


@pytest.mark.parametrize("punctuated", [
    "result=UNPREREGISTERED,",
    "result=UNPREREGISTERED;",
    "result=UNPREREGISTERED.",
])
def test_a_punctuated_unpreregistered_value_is_not_refused(
        tmp_path, monkeypatch, punctuated):
    """REGRESSION (#9075 review P2-3): `(\\S+)` glued punctuation to the value.

    A harmless formatting variation was refused with the misleading message that it "was measured
    against a bar that did not exist". Failing closed is safe in direction, but the diagnosis lied.
    """
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("result=UNPREREGISTERED", punctuated)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []


@pytest.mark.parametrize("exc", [
    FileNotFoundError("fixture"),
    TypeError("a JSONL row that is not a mapping"),
    AttributeError("a fixture that parsed to a list"),
    IndexError("a short row"),
])
def test_every_unreadable_subject_becomes_a_check_error(monkeypatch, exc):
    """REGRESSION (#9075 review P2-1): the tuple only covered four exception types.

    A malformed corpus most plausibly raises TypeError/AttributeError/IndexError, and those
    escaped as an unhandled traceback — the exact "traceback that hides the cause" the exit-2
    contract exists to prevent.
    """
    def boom(_pins):
        raise exc

    monkeypatch.setattr(check, "derive_corpus_facts", boom)
    with pytest.raises(check.CheckError) as excinfo:
        check.run(PREREG_DIR, use_git=False, use_corpus=True)
    assert "cannot re-derive the corpus pins" in str(excinfo.value)
    assert type(exc).__name__ in str(excinfo.value)


def test_the_run_order_claim_is_reported_as_unverified_without_git():
    """REGRESSION (#9075 review P2-4): --no-git must not silently turn an assertion into green."""
    report = check.run(PREREG_DIR, use_git=False, use_corpus=False)
    assert report["run_order_claim"].startswith("UNVERIFIED")
    verified = check.run(PREREG_DIR, use_git=True, use_corpus=False)
    if check.history_unavailable():
        # A shallow checkout has the third outcome, not a verdict: "verified" would be a lie.
        assert verified["ordering"] == check.HISTORY_UNAVAILABLE
        assert verified["run_order_claim"] == check.HISTORY_UNAVAILABLE
    else:
        assert verified["run_order_claim"] == "verified"


@requires_history
def test_the_acceptance_report_states_the_claim_provenance(capsys):
    assert check.main(["--dir", str(PREREG_DIR)]) == 0
    out = capsys.readouterr().out
    assert "run-order claim: verified" in out


@requires_history
def test_the_acceptance_report_claims_a_verified_ordering_only_when_it_checked_one(capsys):
    """The provenance line is the same string the report carries — one source, no paraphrase."""
    assert check.main(["--dir", str(PREREG_DIR)]) == 0
    out = capsys.readouterr().out
    assert "the ordering holds" in out


# ── round-3 review regressions ───────────────────────────────────────────────


@pytest.mark.parametrize("old,new,bad", [
    ("result=UNPREREGISTERED", "RESULT=PASS", "RESULT=PASS"),
    ("verdict=UNPREREGISTERED", "VERDICT=FAIL", "VERDICT=FAIL"),
    ("result=UNPREREGISTERED", "Result = GREEN", "Result=GREEN"),
])
def test_an_uppercase_scored_marker_also_voids_the_exemption(
        tmp_path, monkeypatch, old, new, bad):
    """REGRESSION (#9075 review r3 P2): the whitelist was case-sensitive.

    `RESULT=PASS` / `VERDICT=FAIL` escaped it — case is the last member of the same class of gap the
    r2 fixes closed for spacing and punctuation.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=UNSCORED_LOG.replace(old, new))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and bad in failures[0], (bad, failures)


@pytest.mark.parametrize("glued,bad", [
    ("(verdict=PASS)", "verdict=PASS"),
    ("[result=GREEN]", "result=GREEN"),
    (",verdict=FAIL", "verdict=FAIL"),
    (";result=RED", "result=RED"),
])
def test_a_marker_glued_to_punctuation_also_voids_the_exemption(
        tmp_path, monkeypatch, glued, bad):
    """REGRESSION (#9075 review r4 P2): the prefix anchor demanded WHITESPACE before the key.

    `(verdict=PASS)` matched NOTHING at all — the earlier rounds widened the `=` spacing, the
    trailing punctuation and the case, but never the prefix — so a grandfathered log could carry a
    scored verdict and keep its exemption. That is the exact laundering bound (d) refuses, defeated
    by one character of punctuation (`[result=GREEN]`, `,verdict=FAIL`, `verdict\t=PASS` alike).
    """
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("verdict=UNPREREGISTERED", glued)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and bad in failures[0], (bad, failures)


@pytest.mark.parametrize("bare", ["verdict=", "result = "])
def test_an_unparseable_status_marker_fails_closed(tmp_path, monkeypatch, bare):
    """REGRESSION (#9075 review r4 P2): a marker with no readable value was skipped entirely.

    "Cannot parse" must fail CLOSED. Skipping it lets a log carry a status token the whitelist never
    inspects — the same class of gap, one step further along.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=UNSCORED_LOG + bare + "\n")
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "cannot be parsed" in failures[0], failures


def test_an_exit_marker_glued_to_punctuation_is_seen(tmp_path, monkeypatch):
    """The SAME token anchor on the exit leg: `ran(exit=0)` is an exit marker, not prose."""
    repo = _repo(tmp_path, monkeypatch)
    body = UNSCORED_LOG.replace("exit=3", "ran(exit=0)")
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    # `records exit=` (not `must exit 3`): the token must be READ and refused — "absent" and "wrong
    # value" are different defects, and the pre-fix gate reported the first for this input.
    assert failures and "records exit=" in failures[0], failures


def test_an_uppercase_exit_is_not_accepted_as_three(tmp_path, monkeypatch):
    """`EXIT=0` escaped both regexes; case-insensitivity closes it and still requires the value 3."""
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000,
                     body=UNSCORED_LOG.replace("exit=3", "EXIT=0"))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "must exit 3" in failures[0], failures


@pytest.mark.parametrize("path,value", [
    (("corpus", "planted_units"), 900),
    (("corpus", "value_bearing_units"), 99),
    (("corpus", "hedge_scope_sentences"), 999),
    (("corpus", "sessions"), 70),
    (("corpus", "seed"), 7),
    (("corpus", "planted_operator_edges"), 4),
])
def test_a_record_denominator_that_disagrees_with_the_pins_fails(tmp_path, path, value):
    """REGRESSION (#9075 review r3 P1): the record restated denominators nothing cross-checked.

    The pins are tree-verified; the record is not. A record can therefore disagree with the pins on
    the very denominators the run probe consumes — and `planted_operator_edges = 4` would smuggle the
    forbidden four-edge denominator back in while the gate stayed green.
    """
    rec = _record()
    rec[path[0]] = dict(rec[path[0]])
    rec[path[0]][path[1]] = value
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any(f"{path[0]}.{path[1]}" in f for f in failures), failures


def test_a_record_split_that_disagrees_with_the_pins_fails(tmp_path):
    """The split is a READ discipline; a record that moves a session has changed the protocol."""
    rec = _record()
    rec["split"] = {"tune": ["wp01_quarry_debug"], "held_out": ["wp02_lumen_refactor"]}
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("split.tune" in f for f in failures), failures


def test_a_record_with_the_four_edge_denominator_fails(tmp_path):
    """The sharpest case: the record is what the run probe freezes, so 4 must never reach it."""
    rec = _record(e4_denominator=4)
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("e4_denominator" in f for f in failures), failures


def test_the_real_record_restates_every_pinned_denominator():
    """The record must carry the denominators it is checked against — not silently omit them."""
    import json as _json
    record = _json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    pins = check.parse_pins(PREREG.read_text())
    for key in ("fixtures_hash", "sessions", "seed", "schema_version", "planted_units",
                "value_bearing_units", "hedge_scope_sentences", "planted_operator_edges"):
        assert record["corpus"][key] == pins["corpus"][key], key
    assert record["e4_denominator"] == pins["e4_denominator"] == 15
    assert sorted(record["split"]["tune"]) == sorted(pins["split"]["tune"])
    assert sorted(record["split"]["held_out"]) == sorted(pins["split"]["held_out"])


# ── round-4 review regressions ───────────────────────────────────────────────


@pytest.mark.parametrize("key,value", [
    ("e1_objects", 3),
    ("e1_min_before_share", 0.01),
    ("e2b_surviving_units", 4),
    ("e3_derived_nodes", 3),
    ("e5_rate_min_sentences", 1),
])
def test_a_record_denominator_floor_that_disagrees_with_the_pins_fails(tmp_path, key, value):
    """REGRESSION (#9075 review r4 P2): `denominator_floors` was restated in both copies, unchecked.

    These are the REFUSED boundaries the run probe enforces. `e5_rate_min_sentences: 1` makes the
    8-sentence HELD-OUT half rate-reportable when the pins forbid it; `e2b_surviving_units: 4`
    smuggles the forbidden small denominator back into the machine-consumed artefact.
    """
    rec = _record()
    rec["denominator_floors"] = dict(rec["denominator_floors"])
    rec["denominator_floors"][key] = value
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any(f"denominator_floors.{key}" in f for f in failures), failures


def test_a_record_inventing_a_floor_the_pins_do_not_state_fails(tmp_path):
    """The record may not add a REFUSED boundary the document never argued."""
    rec = _record()
    rec["denominator_floors"] = dict(rec["denominator_floors"])
    rec["denominator_floors"]["e9_made_up"] = 99
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("has no pin" in f for f in failures), failures


#: The pre-registered bundle whose measured log names only FIVE of its seven frozen
#: `hypothesis_bars`. The other two — `corroboration_min_sessions` (which decides the published
#: `not-majority-of-sessions` qualifier) and the emitted-only `sessions_total` — can only be held
#: through `preregistration.json`, so this bundle's document is where the bars are read FROM (one
#: copy: the document's, via the tool's own parser — never a literal here).
HYPOTHESIS_BUNDLE = REPO_ROOT / "docs" / "evidence" / "5087-hypothesis"


def _pins_with_bars() -> dict:
    """The capstone pins plus THIS bundle's real `hypothesis_bars`, as a document would state them."""
    pins = check.parse_pins(PREREG.read_text())
    pins["hypothesis_bars"] = dict(
        check.parse_pins((HYPOTHESIS_BUNDLE / check.PREREG_NAME).read_text())["hypothesis_bars"])
    return pins


@pytest.mark.parametrize("key,value", [
    # The two the measured log cannot hold, so the record is their ONLY copy — the sharp case.
    ("corroboration_min_sessions", 6),
    ("sessions_total", 8),
    # The other five: the log holds them too, but the RECORD's copy is still a second copy a
    # consumer reads, and the finding is about all seven being restated and compared nowhere. A
    # movement that lands in the record alone must red here, not only in the log comparison.
    ("primary_ratio_max", 0.90),
    ("guard_ratio_min", 0.5),
    ("arm_integrity_required", 13),
    ("concentration_max", 0.7),
    ("cost_ratio_max", 2.5),
])
def test_a_record_hypothesis_bar_that_disagrees_with_the_pins_fails(tmp_path, key, value):
    """REGRESSION (#10468 review `f0b6842eb`, P2): the seven frozen bars were restated, unchecked.

    `check_machine_record` compared the corpus keys, the floors, the split, `e4_denominator` and the
    criteria rows — but never `hypothesis_bars`, so the record's own copy of the bars was a second
    copy bound to nothing. It is the ONLY copy of two of them: the measured log names five, and the
    two it does not are `corroboration_min_sessions` (which decides whether the verdict line carries
    `not-majority-of-sessions`) and the emitted-only `sessions_total`. Rotating
    `corroboration_min_sessions` 4 → 6 in the record alone left every check green while a consumer
    of the record read a bar the pinned document does not state.
    """
    bars = dict(_pins_with_bars()["hypothesis_bars"])
    bars[key] = value
    d = _materialise(tmp_path, _record(hypothesis_bars=bars))
    failures = check.check_machine_record(d, _pins_with_bars())
    assert any(f"hypothesis_bars.{key}" in f for f in failures), failures


def test_a_record_dropping_a_pinned_bar_fails(tmp_path):
    """A record that silently drops a bar must red, not skip the comparison (`cmp` is not `if in`)."""
    bars = dict(_pins_with_bars()["hypothesis_bars"])
    bars.pop("corroboration_min_sessions")
    d = _materialise(tmp_path, _record(hypothesis_bars=bars))
    failures = check.check_machine_record(d, _pins_with_bars())
    assert any("hypothesis_bars.corroboration_min_sessions" in f for f in failures), failures


def test_a_record_inventing_a_hypothesis_bar_the_pins_do_not_state_fails(tmp_path):
    """The record may not add a bar the argument never froze — the same direction as the floors."""
    bars = dict(_pins_with_bars()["hypothesis_bars"])
    bars["made_up_bar"] = 99
    d = _materialise(tmp_path, _record(hypothesis_bars=bars))
    failures = check.check_machine_record(d, _pins_with_bars())
    assert any("hypothesis_bars.made_up_bar has no pin" in f for f in failures), failures


def test_a_record_restating_bars_a_pins_block_does_not_state_fails(tmp_path):
    """`hypothesis_bars` is optional at the PINS side (the capstone bundle has no such map), so the
    record of a bundle whose document states no bars may not carry them either."""
    d = _materialise(tmp_path, _record(hypothesis_bars={"primary_ratio_max": 0.75}))
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("hypothesis_bars has no pins block" in f for f in failures), failures


def test_the_real_5087_record_restates_every_frozen_bar():
    """The bundle this guard exists for: its record agrees with its own frozen bars, bar for bar.

    This is the half `tests/test_s3_prior_ablation.py::_threshold_mismatches` delegates — it holds
    the log's five, and says these two are held by the record through this gate.
    """
    pins = check.parse_pins((HYPOTHESIS_BUNDLE / check.PREREG_NAME).read_text())
    assert pins["hypothesis_bars"], "this bundle must state its bars in the pins block"
    assert check.check_machine_record(HYPOTHESIS_BUNDLE, pins) == []
    record = json.loads((HYPOTHESIS_BUNDLE / check.MACHINE_RECORD).read_text())
    assert record["hypothesis_bars"] == pins["hypothesis_bars"]


def test_a_record_sentence_total_that_disagrees_with_the_pins_fails(tmp_path):
    """`hedge_scope_sentences_total` (343) was restated by the record and never compared."""
    rec = _record()
    rec["corpus"] = dict(rec["corpus"])
    rec["corpus"]["hedge_scope_sentences_total"] = 999
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("hedge_scope_sentences_total" in f for f in failures), failures


def test_a_pins_block_missing_a_cross_checked_key_fails_instead_of_skipping(tmp_path):
    """REGRESSION (#9075 review r4 P2): `if key in pins` silently disabled the comparison."""
    pins = check.parse_pins(PREREG.read_text())
    del pins["corpus"]["hedge_scope_sentences_total"]
    d = _materialise(tmp_path, _record())
    failures = check.check_machine_record(d, pins)
    assert any("omits 'hedge_scope_sentences_total'" in f for f in failures), failures


def test_the_pin_total_against_the_derived_sentence_count():
    """The new pin must be tree-derived, not copied from the record it is checked against."""
    pins = check.parse_pins(PREREG.read_text())
    derived = check.derive_corpus_facts(pins)
    assert pins["corpus"]["hedge_scope_sentences_total"] == derived["sentences_total"] == 343


@pytest.mark.parametrize("block", ["pre_existing_unscored_logs: notamap"])
def test_a_wrong_typed_pins_block_is_a_usage_error_not_a_traceback(tmp_path, monkeypatch, block):
    """REGRESSION (#9075 review r4 P3): `dict("notamap")` raised ValueError past `except CheckError`.

    A valid-YAML-wrong-TYPE pins value is a PARSE error, which the contract says is exit 2.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    key = block.split(":")[0]
    pins = {"pre_existing_unscored_logs": {}, "denominator_floors": {}}
    pins[key] = "notamap"
    with pytest.raises(check.CheckError) as excinfo:
        check.check_ordering(prereg, [log], pins)
    assert f"pins.{key} is str" in str(excinfo.value)


def test_a_wrong_typed_denominator_floors_block_is_a_usage_error(tmp_path):
    """The machine-record half of the same guard."""
    rec = _record()
    d = _materialise(tmp_path, rec)
    pins = check.parse_pins(PREREG.read_text())
    pins["denominator_floors"] = "notamap"
    with pytest.raises(check.CheckError) as excinfo:
        check.check_machine_record(d, pins)
    assert "pins.denominator_floors is str" in str(excinfo.value)


def test_the_touch_history_sees_a_merge_commit(tmp_path, monkeypatch):
    """REGRESSION (#9075 review r4 P3): `git log -- path` history-simplified the merge away.

    Without `--full-history` a `--no-ff` merge that touched the exempt log was absent from the
    epochs, so the content bound could miss an edit landed on a side branch.

    ⛔ THE BEHAVIOURAL ASSERTION ALONE DID NOT PIN IT (#10546 review cycle 17, IS-4). Measured on
    this repo: the side edit is `late` on its own, so `failures[0]` is the SAME with or without the
    flag — the test would stay green for a lane that deleted `--full-history`, which is a guard that
    stopped guarding. The two assertions below are the pin, and they are non-vacuous by measurement:
    on this exact history the merge sha appears in `_touch_commits` and NOT in a plain
    `git log --format=%H -- <path>`, so removing the flag fails the second one.

    ⛔ AND WHY THERE IS NO "THE MERGE IS THE ONLY LATE COMMIT" CASE. The reviewer asked for one and
    it cannot be built, measured rather than assumed: `_late_touches` counts a commit only when it
    CHANGED the blob, so a merge that carries content through unchanged is skipped — and a merge
    whose blob differs must have got that content somewhere. If it came from a side commit, that
    side commit is not an ancestor of the registration (else the registration's snapshot would
    already hold it, making the merge blob-equal) and is therefore late too; if the merge itself
    introduced it (an "evil merge"), plain `git log` lists it as well (measured), so `--full-history`
    buys nothing. A merge is therefore never the SOLE late commit — which is exactly why the flag
    had to be pinned directly rather than through a verdict.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    # The registration lands FIRST, then a side branch edits the log and main merges it --no-ff.
    # The bound is TOPOLOGY now (#10529 cycle-9), so the late commit must be a descendant of the
    # registration: an edit that is an ANCESTOR of it was visible to its author and is not a defect.
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    _git(repo, "checkout", "-q", "-b", "side")
    log.write_text(UNSCORED_LOG.replace("after_share=0.6", "after_share=0.25"))
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "side edit", epoch=1_700_000_050)
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side", epoch=1_700_000_200)
    # THE PIN: the merge is TREESAME to the side parent for this path, so history simplification
    # drops it — and `_touch_commits` must still see it, or the content bound can be walked past.
    merge_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert merge_sha in check._touch_commits(log), "the merge landing the edit is invisible"
    simplified = subprocess.run(
        ["git", "log", "--format=%H", "--", str(log.relative_to(repo))],
        cwd=str(repo), capture_output=True, text=True, check=True,
    ).stdout.split()
    assert merge_sha not in simplified, (
        "this history no longer demonstrates the hazard: plain `git log` now shows the merge, so "
        "the `--full-history` pin above proves nothing"
    )
    failures = _armed(prereg, [log], _exempt())
    assert failures and "not an ancestor of" in failures[0], failures


def test_neither_the_cli_nor_run_arms_the_disclosure_by_default(monkeypatch, capsys):
    """The DISARM must survive the seam, not only `check_ordering` (#10546 review cycle 17, IS-1).

    `check_ordering` is where the verdict is decided, but `run()` and `main()` are what CI actually
    invokes — an arm defaulted on at either of those would make the ruling's refusal unreachable no
    matter what the inner function says. Captured at the call boundary, so this costs no bundle.
    """
    seen: list[bool] = []

    def fake_run(*_args, **kwargs):
        seen.append(kwargs.get("allow_atomic_disclosure"))
        return {"document": "d", "rows": [], "evidence_logs": [], "ok": True,
                "failures": [], "ordering": "verified", "run_order_claim": "verified"}

    monkeypatch.setattr(check, "run", fake_run)
    assert check.main(["--dir", "docs/evidence/7949-capstone"]) == 0
    assert check.main(["--dir", "docs/evidence/7949-capstone", "--allow-atomic-disclosure"]) == 0
    capsys.readouterr()
    assert seen == [False, True], seen


def test_run_defaults_to_the_ruled_verdict_not_the_proposal():
    """`run()`'s default is the RULED verdict, and a future lane must change it on purpose.

    `run()` is the seam CI calls. Its default is asserted by signature here rather than by building
    a full bundle (the bundle schema is a separate fixture); `main()`'s wiring of the same flag is
    pinned in `test_neither_the_cli_nor_run_arms_the_disclosure_by_default`. Together they mean the
    arm cannot be switched on by an edit that only touches one of the three layers.
    """
    assert (inspect.signature(check.run)
            .parameters["allow_atomic_disclosure"].default is False)
    assert (inspect.signature(check.check_ordering)
            .parameters["allow_atomic_disclosure"].default is False)


def test_ruled_run_says_unproven_when_the_record_it_could_not_read_mattered(tmp_path, monkeypatch):
    """The RULED path may not report `verified` for an atomic log whose record could not be read.

    With no readable machine record there is nothing to judge the atomic add against, and
    `check_ordering` reports that log through `unreadable_atomic` rather than inventing an ordering
    finding (`check_machine_record` owns the diagnosis and reds the tree). `run_order_claim` is the
    field a machine consumer reads by NAME, so it may not read `verified` beside a half that
    concluded nothing — the defect class the exemption half already qualifies its claim for.
    """
    # The REAL 5087 bundle: it passes every other half of `run()` with `use_corpus=False`, so the
    # ordering claim is the only variable.
    directory = PREREG_DIR.parent / "5087-hypothesis"

    def fake_ordering(prereg, logs, pins, exempted, atomic_added, disclosures, atomic_detail,
                      **_kwargs):
        # The ruled arm's own shape when the record is unreadable: no failure, one unproven log.
        _kwargs["unreadable_atomic"].append("e1-e2.log")
        return []

    monkeypatch.setattr(check, "check_ordering", fake_ordering)
    monkeypatch.setattr(check, "_record_atomic_add", lambda _dir: None)
    report = check.run(directory, use_corpus=False)
    assert report["ordering"].startswith("UNPROVEN"), report["ordering"]
    assert "e1-e2.log" in report["ordering"], report["ordering"]


def test_an_unreadable_record_does_not_erase_the_base_a_refusal_was_measured_against(
    tmp_path, monkeypatch
):
    """REGRESSION (#10546 review cycle 25): `ordering_base` may not read `(not measured)` beside a
    refusal that WAS measured.

    `run()` consults `unreadable_atomic` before `ordering_failures`, so a tree that both carries an
    ordering violation and an unreadable machine record reported the violation while leaving the
    BASE at its default — the field is named for the MEASUREMENT, and the failure lines beside it
    name the ref that did the refusing. The `ordering_failures` arm keeps this true (#10548 r15 F3);
    this is the same fact one arm earlier.
    """
    directory = PREREG_DIR.parent / "5087-hypothesis"

    def fake_ordering(prereg, logs, pins, exempted, atomic_added, disclosures, atomic_detail,
                      **_kwargs):
        _kwargs["unreadable_atomic"].append("e1-e2.log")
        return ["e1-e2.log: not strictly after the pre-registration's add-commit"]

    monkeypatch.setattr(check, "check_ordering", fake_ordering)
    monkeypatch.setattr(check, "_record_atomic_add", lambda _dir: None)
    report = check.run(directory, use_corpus=False)
    assert report["ordering"].startswith("UNPROVEN"), report["ordering"]
    assert report["ordering_base"] not in ("(not measured)", check.PROPOSED_ORDERING_BASE), report


def test_the_armed_rule_reports_its_own_base_and_never_the_ruled_one():
    """`run()` under the PROPOSED rule says which base it did NOT consult (#10546 merge).

    The two rules in `check_ordering` read DIFFERENT histories: the ruled one measures the pair on
    the base ref, the proposal orders it inside this contribution. Reporting the resolved candidate
    on the armed path would be the silent-green `ordering_base` exists to remove — a green line
    saying a measurement was made against a ref the armed rule never opened — so the armed path
    reports its own marker instead.

    Measured on the REAL 5087 bundle (its log's add-commit is a descendant of the registration's, so
    the armed rule proves the order), which is also the only end-to-end exercise of `run(...,
    allow_atomic_disclosure=True)`: the disarm is pinned at the signature and the call boundary
    above, but nothing ran the armed report wiring before the merge.
    """
    directory = PREREG_DIR.parent / "5087-hypothesis"
    if check.history_unavailable():
        pytest.skip("the ordering half needs full history (CI test jobs check out at fetch-depth:1)")
    ruled = check.run(directory, use_corpus=False)
    assert ruled["ordering"] == "verified", ruled
    assert ruled["ordering_base"] != check.PROPOSED_ORDERING_BASE
    armed = check.run(directory, use_corpus=False, allow_atomic_disclosure=True)
    assert armed["ok"] is True, armed["failures"]
    assert armed["ordering"] == "verified", armed["ordering"]
    assert armed["ordering_base"] == check.PROPOSED_ORDERING_BASE, armed["ordering_base"]
    assert armed["ordering_atomic_add"] == [], armed["ordering_atomic_add"]


# ── round-5: shallow CI, glued markers, unreadable status, wrong-typed inputs ──


def test_a_marker_glued_to_punctuation_voids_the_exemption(tmp_path, monkeypatch):
    """`(?:^|\\s)` required whitespace before the key, so a glued marker matched nothing.

    `(verdict=PASS)`, `[result=GREEN]` and `,verdict=FAIL` all survived the exemption. The
    `(?<!\\w)` token anchor catches them while still ignoring `subverdict=`.
    """
    for glue, bad in (
        ("prereg=UNPREREGISTERED\n(verdict=PASS)\nexit=3\n", "verdict=PASS"),
        ("prereg=UNPREREGISTERED\n[result=GREEN]\nexit=3\n", "result=GREEN"),
        ("prereg=UNPREREGISTERED\n,verdict=FAIL\nexit=3\n", "verdict=FAIL"),
    ):
        case = tmp_path / bad.replace("=", "-")
        case.mkdir()
        repo = _repo(case, monkeypatch)
        log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=glue)
        prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
        failures = check.check_ordering(prereg, [log], _exempt())
        assert failures and bad in failures[0], (bad, failures)


def test_a_similar_but_different_key_is_not_a_marker(tmp_path, monkeypatch):
    """`subverdict=PASS` / `myresult=GREEN` are not the probe's vocabulary, so they must not fire."""
    repo = _repo(tmp_path, monkeypatch)
    body = "prereg=UNPREREGISTERED\nsubverdict=PASS myresult=GREEN\nexit=3\n"
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []


def test_an_unreadable_status_fails_closed(tmp_path, monkeypatch):
    """A `verdict=` with no parseable value must fail, not be skipped as absent."""
    repo = _repo(tmp_path, monkeypatch)
    body = "prereg=UNPREREGISTERED\nverdict=\nexit=3\n"
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000, body=body)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and (
        "cannot be parsed" in failures[0] or "carries verdict=" in failures[0]), failures


def test_a_shallow_checkout_reports_unprovable_not_a_violation(tmp_path, monkeypatch):
    """REGRESSION (#9075 review r5): CI test jobs check out at fetch-depth:1.

    Git GRAFTS a depth-1 clone at its tip, so every path resolves to HEAD's epoch and "violated"
    becomes indistinguishable from "unprovable". Reporting the first reds a correctly ordered tree in
    every CI test job — a false RED blamed on the wrong thing.
    """
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    repo = tmp_path / "repo"
    repo.mkdir()
    prereg = repo / check.PREREG_NAME
    prereg.write_text("x\n")
    with pytest.raises(check.HistoryUnavailable) as excinfo:
        check.check_ordering(prereg, [])
    assert "UNPROVABLE" in str(excinfo.value)


def test_the_real_artefact_skips_rather_than_reds_when_history_is_unavailable(monkeypatch):
    """The CI-visible half: a shallow checkout must SKIP, not fail the required check."""
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    report = check.run(PREREG_DIR, use_git=True, use_corpus=True)
    assert report["ok"], report["failures"]
    assert report["ordering"].startswith("UNPROVABLE")
    assert report["run_order_claim"] == report["ordering"]


@pytest.mark.parametrize("key,value", [
    ("corpus", "not-a-dict"),
    ("denominator_floors", "n/a"),
    ("split", 7),
])
def test_a_wrong_typed_record_block_is_a_usage_error_not_a_traceback(tmp_path, key, value):
    """REGRESSION (#9075 review r5 P2): the record side was unguarded.

    `record["corpus"] = "not-a-dict"` raised ``AttributeError: 'str' object has no attribute 'get'``
    — the same class the pins-side guard closed, applied incompletely.
    """
    rec = _record()
    rec[key] = value
    d = _materialise(tmp_path, rec)
    with pytest.raises(check.CheckError) as excinfo:
        check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert key in str(excinfo.value)


def test_a_wrong_typed_salience_labels_pin_is_a_usage_error():
    """The pins block's `salience_labels` sub-block was the last unguarded `.get()`."""
    pins = check.parse_pins(PREREG.read_text())
    pins["salience_labels"] = "notamap"
    with pytest.raises(check.CheckError) as excinfo:
        check.check_pins(pins, check.derive_corpus_facts(
            check.parse_pins(PREREG.read_text())))
    assert "salience_labels" in str(excinfo.value)


def test_a_non_mapping_criteria_entry_is_reported_not_fatal(tmp_path):
    """`criteria: ["E1"]` must be an attributed finding, not an AttributeError."""
    rec = _record()
    rec["criteria"] = ["E1"]
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("expected a mapping" in f for f in failures), failures


# ── round-6 review regressions ───────────────────────────────────────────────


def test_the_laundering_whitelist_still_runs_when_history_is_unavailable(tmp_path, monkeypatch):
    """REGRESSION (#9075 review r6 P2-1): the history guard ran BEFORE the git-free whitelist.

    CI test jobs are ALWAYS shallow, so the one defense against a scored number — which reads the
    log's own lines and needs no git — was disabled in every CI run. The whitelist must run first,
    and its findings must survive the unprovable-history verdict.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000,
                     body=UNSCORED_LOG.replace("verdict=UNPREREGISTERED", "verdict=PASS"))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)

    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    with pytest.raises(check.HistoryUnavailable) as excinfo:
        check.check_ordering(prereg, [log], _exempt())
    carried = excinfo.value.failures
    assert any("verdict=PASS" in f for f in carried), carried


def test_a_laundered_log_reds_even_in_a_shallow_checkout(monkeypatch):
    """End to end: the shallow verdict must not turn a laundering finding into a green."""
    real = (PREREG_DIR / "e1-e2.log").read_text()
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    monkeypatch.setattr(
        check, "_log_declares_unscored",
        lambda _log: (False, "carries verdict=PASS — fabricated"),
    )
    report = check.run(PREREG_DIR, use_git=True, use_corpus=False)
    assert not report["ok"], "a carried whitelist failure must survive the shallow verdict"
    assert any("verdict=PASS" in f for f in report["failures"]), report["failures"]
    assert report["ordering"].startswith("UNPROVABLE")
    assert real  # the real log is untouched by this test


def test_an_absent_exemption_still_fails_when_history_is_unavailable(monkeypatch):
    """The `exempt - present` check is also git-free, so it must also run before the guard."""
    pins = check.parse_pins(PREREG.read_text())
    pins["pre_existing_unscored_logs"]["ghost.log"] = {"verdict": check.UNSCORED}
    monkeypatch.setattr(check, "history_unavailable", lambda: True)
    prereg = PREREG_DIR / check.PREREG_NAME
    with pytest.raises(check.HistoryUnavailable) as excinfo:
        check.check_ordering(prereg, sorted(PREREG_DIR.glob("*.log")), pins)
    assert any("not in this directory" in f for f in excinfo.value.failures)


@pytest.mark.parametrize("part", ["metric", "denominator", "threshold"])
def test_a_record_criterion_cell_that_disagrees_with_the_table_fails(tmp_path, part):
    """REGRESSION (#9075 review r6 P2-2): only `threshold` was compared.

    `criteria[E4].denominator = "the 4 planted operator edges"` passed with the gate green — the
    record is what the run probe freezes, so a paraphrase there is a second copy that can disagree.
    """
    rec = _record()
    rec["criteria"] = [dict(c) for c in rec["criteria"]]
    for crit in rec["criteria"]:
        if crit["id"] == "E4":
            crit[part] = "the 4 planted operator edges"
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any(f"{part} for E4" in f for f in failures), failures


def test_a_four_edge_rate_in_the_machine_record_is_refused(tmp_path):
    """The forbidden denominator may not reach the artefact the probe freezes."""
    rec = _record()
    rec["criteria"] = [dict(c) for c in rec["criteria"]]
    for crit in rec["criteria"]:
        if crit["id"] == "E4":
            crit["metric"] = "edges wired, reported as 2/4"
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("four-edge denominator rate" in f for f in failures), failures


def test_the_real_record_carries_the_tables_own_cells():
    """No paraphrase: the record is generated from the table, not retyped beside it."""
    import json as _json
    record = _json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    table = {r["id"]: r for r in check.parse_criteria_rows(PREREG.read_text())}
    for crit in record["criteria"]:
        row = table[crit["id"]]
        for part in ("metric", "denominator", "threshold"):
            assert crit[part] == row[part], (crit["id"], part)


def test_a_missing_git_binary_does_not_crash_the_ordering(monkeypatch, tmp_path):
    """REGRESSION (#9075 review r6 P3): `_is_tracked` was the one unguarded subprocess call."""
    import subprocess as sp

    def no_git(*_a, **_k):
        raise FileNotFoundError("git")

    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log], _exempt()) == []
    monkeypatch.setattr(sp, "run", no_git)
    # With git gone the epochs are unreadable; the contract is an attributed failure, not a crash,
    # and never a silent pass.
    try:
        result = check.check_ordering(prereg, [log], _exempt())
    except check.CheckError:
        return
    assert result, "an unreadable history must not report a clean ordering"


# ── round-5/6 review regressions ─────────────────────────────────────────────


def test_deleting_e1_cannot_be_masked_by_the_e1b_control():
    """REGRESSION (#9075 review r3 P2): `startswith` let `E1b` stand in for a deleted `E1`.

    `E1b` is E1's ANTI-GAMING control, so a table can lose the epic's central criterion *and* the
    `after_share <= 0.50 x before_share` bar while the completeness check stays green — the guard
    keeps the check satisfied for the row it guards. Only `E2` accepts sub-rows.
    """
    body = _rows(
        "| E1b | planted units surviving | the **90** planted units | `>= 90 of 90` |",
        "| E2a | value units surviving | the **14** value units | `>= 13 of 14` |",
        "| E2b | fused claims | the planted units that survived | `<= 0.05` |",
        "| E3 | anchored derived nodes | all derived nodes (floor ≥ 30) | `= 1.000` |",
        "| E4 | operator edges | the **15** edges | `= 15 of 15` |",
        "| E5 | marker claims | **29** sentences | `>= 0.90` ratio |",
    )
    failures = check.check_completeness(check.parse_criteria_rows(body))
    assert any("criterion E1 has no row" in f for f in failures), failures
    # The sub-row allowance is NOT narrowed: E2 is still satisfied by E2a/E2b.
    assert not any("criterion E2 has no row" in f for f in failures), failures


def test_the_operator_denominator_comes_from_the_corpus_own_function(monkeypatch):
    """REGRESSION (#9075 review r1 P2): the tool re-encoded the operator-edge denominator.

    `corpus.planted_operator_count()` calls itself "the ONE source for an operator-edge DENOMINATOR
    in tests and lanes", and §1.1 names it as the derivation method — so an inline re-derivation is
    a second encoding that has to be kept in sync by hand.
    """
    from tests.eval.write_path import corpus

    monkeypatch.setattr(corpus, "planted_operator_count", lambda ids=None, root=None: 4242)
    pins = check.parse_pins(PREREG.read_text())
    assert check.derive_corpus_facts(pins)["planted_operator_edges"] == 4242


def test_the_fixtures_are_read_through_the_corpus_loader(monkeypatch):
    """The same seam one layer down: fixtures are `corpus.load_fixture`, not a hand-rolled read."""
    from tests.eval.write_path import corpus

    def boom(*_a, **_k):
        raise AssertionError("load_fixture is the corpus package's reader")

    monkeypatch.setattr(corpus, "load_fixture", boom)
    with pytest.raises(AssertionError, match="load_fixture"):
        check.derive_corpus_facts(check.parse_pins(PREREG.read_text()))


def test_an_exclusion_that_excludes_nothing_is_a_stale_pin():
    """REGRESSION (#9075 review r5 note 1): only the *absent* id was checked, not the idle one.

    §3.3 says both named exclusions are re-derived. An exclusion that exists but whose anchor
    carries no digit excludes nothing from the value-bearing class, so it is a pin from a corpus
    that is no longer there — and the real pins are digit-bearing today.
    """
    pins = check.parse_pins(PREREG.read_text())
    pins["corpus"]["value_excluded_unit_ids"] = [
        *pins["corpus"]["value_excluded_unit_ids"], "wp01_quarry_debug_u_02"]
    failures = check.check_pins(pins, check.derive_corpus_facts(pins))
    assert any("carries no digit" in f for f in failures), failures
    assert any("wp01_quarry_debug_u_02" in f for f in failures), failures


def test_the_four_edge_refusal_is_a_rate_not_a_count():
    """REGRESSION (#9075 review r5 note 2): the claim was "any rate phrasing" for a rate regex.

    §1.4 narrates the history as `swung 0, 1, 1, 1, 2 of 4 on IDENTICAL code`; refusing that form
    would red the document for stating its own reasoning. The refused thing is the RATE.
    """
    assert not check.FOUR_EDGE_RATE.search("swung 0, 1, 1, 1, 2 of 4 on identical code")
    assert check.FOUR_EDGE_RATE.search("we measured 2/4 on identical code")


def test_a_log_beside_the_probes_is_not_in_the_ordering_set(tmp_path):
    """#9075 review r5 note 3: the glob is NON-RECURSIVE, and that is stated, not accidental.

    A re-run's log would naturally land under `probes/`; the gate orders the logs of the evidence
    directory itself, which is what §7 says ("each `*.log` in this directory").
    """
    (tmp_path / "probes").mkdir()
    (tmp_path / "probes" / "rerun.log").write_text("x\n")
    (tmp_path / "e9.log").write_text("x\n")
    assert sorted(p.name for p in tmp_path.glob(check.EVIDENCE_LOG_GLOB)) == ["e9.log"]


def test_a_record_without_the_document_pin_fails(tmp_path):
    """REGRESSION (#9075 review r3 P3): the argument itself was the one unbounded artefact.

    `--diff-filter=A` takes the OLDEST add-epoch, so a delete-and-re-add rewrite of the document
    keeps the old epoch; a shallow checkout has no epochs at all. The digest is git-free.
    """
    rec = _record()
    rec.pop(check.DOCUMENT_SHA_PIN)
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any(
        check.DOCUMENT_SHA_PIN in f and "carries no" in f for f in failures
    ), failures


def test_a_record_describing_a_different_document_fails(tmp_path):
    rec = _record(**{check.DOCUMENT_SHA_PIN: "sha256:" + "0" * 64})
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("re-edited without the record being re-cut" in f for f in failures), failures


def test_a_rewritten_document_without_a_re_cut_record_fails(tmp_path):
    """The failure the pin exists for: a threshold edited AFTER a scored log, record untouched."""
    d = _materialise(tmp_path, _record())
    doc = d / check.PREREG_NAME
    doc.write_text(doc.read_text().replace("<= 0.50 × before", "<= 0.90 × before"))
    failures = check.check_machine_record(d, check.parse_pins(doc.read_text()))
    assert any("re-edited without the record being re-cut" in f for f in failures), failures


def test_the_real_record_pins_the_real_document():
    """The pinned digest is the document's, not a copy that can drift."""
    import json as _json

    record = _json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    assert record[check.DOCUMENT_SHA_PIN] == (
        "sha256:" + hashlib.sha256(PREREG.read_bytes()).hexdigest()
    )


def test_a_skipped_corpus_half_is_reported_not_hidden(capsys):
    """REGRESSION (#9075 review r6 P1): `--no-corpus` is what CI's ordering step runs.

    A report that reads as fully checked while one half never ran is the silent-green shape this
    tool exists to refuse, so the skipped half appears in the verdict line.
    """
    assert check.main(["--dir", str(PREREG_DIR), "--no-corpus", "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "corpus pins NOT checked" in out
    assert "the pins match the tree" not in out


#: The CI invocation of the ordering half, verbatim — the whole command, not a substring of it.
ORDERING_HALF_INVOCATION = (
    # Re-cut 2026-11 (#10468 r6 F1): a SECOND pre-registered bundle was added to this one step,
    # because no CI job read `docs/evidence/5087-hypothesis/` at all — its ordering rule and its
    # record↔pins cross-check never executed, and a docs-only diff (which runs only the `tier1`
    # slice) is exactly the change that can move a threshold after the numbers landed. `--dir` is
    # repeatable, so this stays ONE command on ONE line with no shell operator between two runs
    # (an `&&` chain would be a second command, and the pin refuses to describe two).
    # Re-cut 2026-11 (#10463): `--base-ref` names the branch the PR merges INTO. The ordering claim
    # is now "the registration was PUBLISHED on the base before the log's add-commit", so the base is
    # an INPUT the step must pass — inferring `origin/main` would answer the wrong question on a PR
    # that targets another branch, and a stale/absent base must reach the tool as such.
    # `|| 'main'` is load-bearing (review cycle 2, finding 2): on a `push` event `github.base_ref` is
    # empty, so the bare form renders `origin/` — unresolvable — and this unconditional step would
    # exit 2 on every push to `main`.
    "python3 tools/capstone_preregistration_check.py "
    "--dir docs/evidence/7949-capstone --no-corpus --dir docs/evidence/5087-hypothesis "
    '--base-ref "origin/${{ github.base_ref || \'main\' }}"'
)

#: The only `if:` forms that do not drop enforcement. Judged on its own (`_SAFE_STEP_KEYS`
#: deliberately does not contain `if:`), because a `changes` job whose earlier steps can fail needs
#: `if: always()` to keep the gate running — the repo's other gate-step pins bless it too
#: (`tests/test_ci_selection.py`).
_ALWAYS_RUN = ("always()", "${{ always() }}")
#: The only step keys that can neither skip the step nor mask its exit code. An ALLOW-list, not an
#: enumeration of the silencing keys: `continue-on-error:` skips a real failure, `shell:` swallows
#: its exit code, `working-directory:` can point at a stub tree, and `env:` (a PATH shim) shadows
#: `python3` so the real check never runs. Enumerating them one at a time is what left the next one
#: open (`tests/test_merge_throughput.py` records that measurement for its own gate step). `if:` is
#: absent on purpose and handled by `_ALWAYS_RUN` instead.
_SAFE_STEP_KEYS = {"name", "run", "id", "timeout-minutes"}

#: `env:` keys permitted above the gate step, per scope. An interpreter or shell hook needs no
#: `PATH` edit at all, so a `PATH`-only test is the same enumeration trap one key over: `BASH_ENV`
#: is sourced by the non-interactive bash a `run:` uses, a `PYTHONPATH` carrying a `sitecustomize.py`
#: runs at interpreter startup, and `LD_PRELOAD` loads before either — every one of them can exit 0
#: with the real check never running. So the KEY SET is an allow-list: a new key is a deliberate
#: edit here, not a silent one. The `changes` job carries no `env:` of its own today.
_SAFE_WORKFLOW_ENV = frozenset({"TORTOISE_SECRET_PEPPER", "TORTOISE_FAST_ATEXIT", "PYTEST_XDIST_WORKERS"})
_SAFE_JOB_ENV: dict[str, frozenset[str]] = {}

#: The runner the gate's job AND the required aggregate must stay on. `container:` is refused
#: because it replaces the image the gate runs in; `runs-on:` is the same knob one key over, and a
#: runner whose `python3` is a stub exits 0 with the real check never running.
_EXPECTED_RUNS_ON = "ubuntu-latest"

#: What the required aggregate's steps must still contain: the sweep that turns a failed need into
#: a red required check. `needs` membership alone only says a red leg FEEDS the aggregate — not that
#: the aggregate still reports it.
_EXPECTED_AGGREGATE_MARKERS = ("needs.*.result", "failure")

#: Whichever job owns the gate must have its steps declared here, as (label, digest) pairs, in
#: order — the gate step is the sentinel `_GATE_SENTINEL`, because its own definition is pinned by
#: its allow-list (so `if: always()` and `id:`/`timeout-minutes` stay permitted) while its POSITION
#: is pinned here. The step set alone is not enough, and neither is a name: a sibling that keeps its
#: name and its first `run:` token but rewrites its script can restore `docs/evidence/7949-capstone`
#: from another ref, or drop a stub `python3` onto `PATH`, without touching the gate step at all.
#: Nothing else pins this job's step list, so each sibling's whole definition is pinned: any edit
#: here reds the pin and must be re-cut deliberately. Moving the gate to a peer job means declaring
#: that job here; an undeclared owner is a violation, not a pass.
#:
#: The required aggregate (`python-ci-gate`) is declared here for the same reason, one scope out:
#: its job-level `env:`/`defaults.run.*` refusals cannot see a step APPENDED to the job, and a
#: membership test over its script cannot see that script being replaced by one that still carries
#: the marker words (`echo 'needs.*.result failure'`) while the real sweep is deleted. Its steps are
#: therefore pinned by whole-definition digest too — no sentinel, because that job has exactly one
#: step and every byte of it is load-bearing.
#:
#: Re-cut with (set `job` to the job whose step list changed):
#: `python3 -c "import json,hashlib,yaml; w=yaml.safe_load(open('.github/workflows/python-ci.yml'));
#: job='changes'; print([(s.get('uses') or s.get('name'), hashlib.sha256(json.dumps(s,sort_keys=True,
#: ensure_ascii=False).encode()).hexdigest()) for s in w['jobs'][job]['steps']])"`
#:
#: Re-cut history — the digest is over the WHOLE definition, so every one of these is a deliberate
#: re-cut, never a refresh-to-green. Append a line when you re-cut, or the next stale digest is
#: indistinguishable from a silencing edit somebody rubber-stamped:
#:   #9617 — `Tiered selection` + `Emit push matrix (#1472/#6135)`, from #9542 (#9539): the leg set
#:           is now read from `tools/ci_selection.slow_leg_files()` instead of the workflow's own
#:           matrix, and `slow_matrix` is emitted to `$GITHUB_OUTPUT`. No `$GITHUB_PATH`/
#:           `$GITHUB_ENV` write, no exit-code swallowing — the sibling-shim check above stays quiet.
#:   #9551 — `python-ci-gate` → `Aggregate matrix result`: ONE row appended to the LEGS heredoc
#:           (`test-record-postgres|…|-`), so the new armed record leg fails closed by name instead of
#:           certifying a `skipped` shard. No `$GITHUB_ENV`/`$GITHUB_PATH` write, no exit-code
#:           swallowing, no early-return added; the sweep's marker words are unchanged.
#:   #10228 — `Tiered selection` + `Emit push matrix (#1472/#6135)` re-cut, and `Non-gating lanes —
#:           what this gate does NOT run (#10194)` added, from #10206: the selector now derives the
#:           gating slow pool as `slow_gating_files()` (slow_files − carve-out − the non-gating
#:           eval/longmem lane). The new step runs `python3 tools/ci_selection.py
#:           --non-gating-report`, which is REPORT-ONLY (`action="store_true"`, prints and exits 0) —
#:           no `$GITHUB_ENV`/`$GITHUB_PATH` write, no exit-code swallowing, no early return. It sits
#:           BEFORE the gate sentinel, and its whole definition is pinned here precisely because of
#:           that position: it cannot be rewritten into a tree edit without reding this allow-list.
#:           The gate step itself is unchanged (sentinel) and keeps its position.
#:   #9984 — `python-ci-gate` → `Aggregate matrix result`: ONE row appended to the LEGS heredoc
#:           (`ci-introduced-failure|…|-`) plus a `failure|timed_out` case that DELEGATES a failed
#:           test arm to that job instead of reddening on it alone. The job still has exactly ONE
#:           step — no checkout, no `uses:`, no `env:`, no `$GITHUB_ENV`/`$GITHUB_PATH` write, no
#:           exit-code swallowing, no early return; the sweep's marker words are unchanged and its
#:           reachability is preserved (it is gated on `RED_LEGS` being empty, i.e. it still fires
#:           for a need no row claimed). The predicate itself lives in a SEPARATE job precisely so
#:           this pin's allow-list never has to widen.
#:   #9984 review — `python-ci-gate` → `Aggregate matrix result`: the identity-blind sweep's
#:           `cancelled` half is now swept UNCONDITIONALLY (it could never be delegated, so gating
#:           it on `RED_LEGS` being empty only blinded this layer to a leg that never ran). Still
#:           ONE step, still no `env:`/`uses:`/`$GITHUB_ENV` write, no exit-code swallowing, no early
#:           return; the `failure|cancelled` sweep stays a literal line of code (#9240). The sibling
#:           half of the same review — the verdict STEP's `if:` naming `timed_out` — is in the
#:           `ci-introduced-failure` job and is pinned (step key allow-list) by
#:           `tests/test_ci_new_failure_gate.py`, not by this digest.
#:   #10206 (re-cut on rebase of #10202) — `changes`: three entries, all from #10206
#:           (`646d30f9e1ef`), which edited the `changes` job without re-cutting this allow-list and
#:           so left `main` red on this pin (measured: 46 failures of `test_the_ci_pin_*` at
#:           `origin/main`, reproduced on a clean main worktree).
#:             • `Non-gating lanes — what this gate does NOT run (#10194)` — a NEW step, `{name, run}`
#:               only, no `uses:`/`env:`/`$GITHUB_ENV`/`$GITHUB_PATH` write and no exit-code
#:               swallowing; it prints the non-gating lane and exits 0 on every path, and its POSITION
#:               is pinned where the workflow declares it (between `Emit push matrix` and `Tier-2
#:               duration split`).
#:             • `Tiered selection` + `Emit push matrix (#1472/#6135)` — digest-only movement, and it
#:               is COMMENT-only: `_code()` is byte-identical before and after #10206 and the key
#:               set is unchanged (`id`/`name`/`run`), because the edit only rewrote the prose
#:               (`slow_files - carve_out` → `slow_gating_files()`). Verified by diffing `_code()`
#:               at `646d30f9e1ef^` against `origin/main`, not by reading the patch.
#:   #9984 review (2) — `python-ci-gate` → `Aggregate matrix result`: the identity-blind
#:           sweep's `failure` half stops being gated on `RED_LEGS` being EMPTY and starts
#:           being gated on a COUNT (`RESULT_FAILS > ROW_FAILS`), because emptiness asked
#:           the wrong question — it held whenever ANY leg was delegated, which blinded
#:           the layer to a `failure` need that had no row of its own. Still ONE step,
#:           still no `env:`/`uses:`/`$GITHUB_ENV`/`$GITHUB_PATH` write, no exit-code
#:           swallowing, no early return; `grep -qE 'failure|cancelled'` survives as a
#:           literal line of code (#9240).
#:   #10186 — `changes`: `Emit push matrix (#1472/#6135)` + `Tier-2 duration split (#1473)` — digest-only
#:           movement, and it is the `env:` block being MOVED, not added or widened. #6897's
#:           `CI_SHARD_ROUTING` opt-in was at JOB scope; this pin's `_SAFE_JOB_ENV` is empty and names
#:           this job as the reason, because any key at job scope hangs over this gate step and can
#:           hook the interpreter (`BASH_ENV`, a `PYTHONPATH` `sitecustomize.py`, `LD_PRELOAD`,
#:           `PATH`) — so `ci_ordering_half_violations` returned one violation on the real workflow and
#:           red a REQUIRED fast shard (`test (e)`, run `41002293616`). The SAME single key now sits on
#:           the two steps that emit `fast_matrix` and nowhere else: both `run:` scripts are byte-identical
#:           (`_code()` unchanged), neither writes `$GITHUB_PATH`/`$GITHUB_ENV`, neither swallows an exit
#:           code, and the step set and order are unchanged — so the sibling-shim, shim and `_ALWAYS_RUN`
#:           checks stay quiet and this allow-list is not widened. The lever still reaches every emitter,
#:           pinned by `tests/test_ci_selection.py::test_the_opt_in_reaches_every_matrix_emitter_in_the_changes_job`,
#:           and job scope is now forbidden by `test_the_changes_job_carries_no_job_scope_env`.
#:   #10897 — `changes`: `Emit push matrix (#1472/#6135)` + `Tier-2 duration split (#1473)` — digest-only
#:           movement, and like #10186 it is the `env:` block WIDENED (two keys added beside
#:           `CI_SHARD_ROUTING`), not the `run:` script. `_code()` is byte-identical on both steps;
#:           neither writes `$GITHUB_PATH`/`$GITHUB_ENV`, neither swallows an exit code, there is no
#:           `uses:`/`shell:`/`defaults.run` change and the step set/order is unchanged, so the
#:           sibling-shim, shim and `_ALWAYS_RUN` checks stay quiet and `_SAFE_JOB_ENV` stays empty
#:           (still STEP scope only, which is why both emitters carry the keys instead of the job).
#:           The keys are `CI_PR_LABELS` (the PR's labels, empty on push) and `CI_FOLD_RUNNER_LABELS`
#:           (the operator's declared fold runner class); the DECISION is
#:           `tools/ci_selection.py::resolve_fold_runner`, so the lever is unit-tested and an unusable
#:           declaration falls back to the row's own runner rather than routing a required check onto
#:           a label no runner carries. Reaches every emitter, pinned by
#:           `tests/test_ci_selection.py::test_the_fold_lever_reaches_every_matrix_emitter_in_the_changes_job`.
_GATE_SENTINEL = "<the gate step — pinned by its own allow-list>"
_EXPECTED_JOB_STEPS: dict[str, tuple[tuple[str, str], ...]] = {
    "changes": (
        ("actions/checkout@v4",
         "19a6bf0668d5596ebf1abfe2059cac9d073f2c13eb8a46031082417054f593a1"),
        ("actions/setup-python@v5",
         "ee8a8309fa4269ec6b9663eea20dfa7a9fa85d8999e1f89c6a83a1a9b4f692ad"),
        ("Install pyyaml",
         "f3950abdebf4f38e23e27bdc3a983fe91bafd42478e7ec5e5a36d8718ff6d516"),
        ("Tiered selection",
         "3e532b44ae1d76d09b0557c32139fdc8f0546fe5ed92bd11d1d4130f0a8d6ce8"),
        ("Emit push matrix (#1472/#6135)",
         # Re-cut 2026-11-04 (PR for #10897), env-only reason again: `CI_PR_LABELS` +
         # `CI_FOLD_RUNNER_LABELS` added beside `CI_SHARD_ROUTING`, `run:` byte-identical.
         "7428e8235db6656dd3c60f02dd4852ca8b4c742c75dc3539bf994a1a314591dd"),
        ("Non-gating lanes — what this gate does NOT run (#10194)",
         "9b6ec88d732ebef44f83fa29380ed9f73d8d9daa01cc61964939db0a1bd26bc7"),
        ("Tier-2 duration split (#1473)",
         # Re-cut 2026-11-04 (PR for #10897), same env-only reason as the push emitter
         # above: this emitter's output OVERRIDES the push matrix on tier-2 PRs, so a
         # lever on the push step alone is dead on exactly the event every PR uses.
         "b9e84082afe9b62fdfe39bce221464e88b1e24d63d2525ffd069bd4d94e64cab"),
        ("Capstone pre-registration gate — the ordering half (#9075)", _GATE_SENTINEL),
        ("Secrets scan (evidence artifacts,",
         "81dbfb18f5279f51455a71da92c66a6754fa01a497df1b43be5f974af62569ce"),
    ),
    # The only `merge_condition`'s own step: a step added here exports `BASH_ENV` through
    # `$GITHUB_ENV` for the aggregate step, and replacing this step with a marker-carrying `echo`
    # deletes the whole failure sweep. Both leave every other check in this pin clean.
    "python-ci-gate": (
        ("Aggregate matrix result",
         "c40947981e1f62d77e17de090244ed6e09a5877f6c61a32fbb87b31f291772d7"),
    ),
}


def _code(step: dict) -> str:
    """The step's `run:` with comment lines removed — a `#`-prefixed mention is not an invocation."""
    return "\n".join(
        line for line in str(step.get("run") or "").splitlines()
        if not line.strip().startswith("#")
    )


def _step_digest(step: dict) -> str:
    """sha256 over a step's WHOLE definition — every key and every line of its `run:`.

    A name+first-token signature is not enough: a sibling that keeps both and rewrites its script
    still neutralises the gate (measured), which is the same enumeration trap one level out.
    """
    return hashlib.sha256(
        json.dumps(step, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _step_set_violation(job: dict, job_label: str, expected_steps, sentinel=None) -> str | None:
    """`None` when the job's step list IS the declared one, else the message saying how it differs.

    `sentinel` is the gate step, whose own definition is pinned by its allow-list (so an
    `if: always()` guard or an `id:`/`timeout-minutes` stays permitted) while its POSITION is pinned
    here; every other step is compared by WHOLE definition. A name+first-token signature is not a
    signature: a sibling that keeps both and rewrites its script still neutralises the gate, and
    the required aggregate's only step can be replaced by an `echo` that carries the marker words
    while the real failure sweep is deleted (both measured).
    """
    got = [
        (str(s.get("uses") or s.get("name") or "<unnamed>"),
         _GATE_SENTINEL if s is sentinel else _step_digest(s))
        for s in job.get("steps") or []
    ]
    if got == list(expected_steps):
        return None
    # Counter, not `dict`: two steps can share a label, and a `dict` would collapse them —
    # so a step ADDED under an existing label would read as an empty added/removed pair and
    # be reported as an ordering problem, misdescribing exactly the malicious case.
    got_labels = Counter(label for label, _ in got)
    expected_labels = Counter(label for label, _ in expected_steps)
    expected_by_label = dict(expected_steps)
    got_by_label = dict(got)
    added = sorted((got_labels - expected_labels).elements())
    removed = sorted((expected_labels - got_labels).elements())
    rewritten = sorted(
        label for label in set(got_labels) & set(expected_labels)
        if got_labels[label] == expected_labels[label]
        and got_by_label[label] != expected_by_label[label]
    )
    if added or removed or rewritten:
        return (
            f"{job_label}'s steps changed — added {added}, removed "
            f"{removed}, rewritten {rewritten}: a sibling step can rewrite the tool or "
            "restore the evidence tree from another ref without touching the gate step's "
            "shape (if the edit is intended, re-cut this allow-list deliberately)"
        )
    # Same steps, different order: the message must still say what changed, or it reads
    # as a violation that names nothing (a step moved above the checkout/gate is not
    # cosmetic — a step before the gate can rewrite the tree it evaluates).
    return (
        f"{job_label}'s steps are out of order — got "
        f"{[label for label, _ in got]}, expected "
        f"{[label for label, _ in expected_steps]}: a step placed before the gate step "
        "can rewrite the tree it evaluates"
    )


def _loose_pin_holds(workflow: dict) -> bool:
    """The PRE-FIX pin (#9075 review, ISSUE 1) — the four assertions at `origin/main` verbatim.

    It was: the `changes` job checks out, every checkout uses `fetch-depth: 0`, and the joined
    `run:` scalars mention the tool and the `--dir`. Kept only so the tests below can prove each
    silencing mutation really was invisible to it — the point of the rewrite is that this
    predicate is green on a gate that can never fail a PR.

    The `fetch-depth` half is reproduced faithfully on purpose: a shallow checkout WAS caught
    before this rewrite, so modelling it here as unpinned would overstate the pre-fix gap. That
    mutation is therefore a separate still-caught case (`test_the_ci_pin_still_catches_*`), not
    one of the `_mutations()` cases whose whole premise is "the old pin stayed green".
    """
    steps = workflow["jobs"]["changes"]["steps"]
    checkouts = [
        s for s in steps if str(s.get("uses", "")).startswith("actions/checkout")
    ]
    if not checkouts:
        return False
    if any((s.get("with") or {}).get("fetch-depth") != 0 for s in checkouts):
        return False
    runs = " ".join(str(s.get("run", "")) for s in steps)
    return (
        "tools/capstone_preregistration_check.py" in runs
        and "--dir docs/evidence/7949-capstone" in runs
    )


def ci_ordering_half_violations(workflow: dict) -> list[str]:
    """Every way the ordering half's enforcement can be dropped or silenced, as messages.

    Returns `[]` only when, simultaneously: exactly one step invokes the gate and does so with the
    exact expected single-line command; that step carries no key that can skip it or mask its exit
    code; no scope above it hooks the interpreter or shell (`env:`, `defaults.run.*`) or adds a step
    outside the declared set; the owning job checks out at `fetch-depth: 0` (the ordering half reads
    commit epochs) and feeds the required aggregate; and that aggregate can still report a red leg —
    it is the only `merge_condition`, so a silenced aggregate is a merge-green red gate. That
    aggregate gets the same treatment one scope out: its job keys, its STEP-key allow-list, and its
    STEP LIST pinned by whole definition.

    Split out of the pin so each silencing idiom can be tested in the failing direction: a
    membership assertion cannot see `|| true`, and that is exactly the defect this closes.
    """
    violations: list[str] = []
    jobs = workflow.get("jobs") or {}

    owners = [
        (name, job, step)
        for name, job in jobs.items()
        for step in job.get("steps") or []
        if "tools/capstone_preregistration_check.py" in _code(step)
    ]
    if len(owners) != 1:
        return [
            f"expected exactly ONE step running tools/capstone_preregistration_check.py, found "
            f"{len(owners)} ({[name for name, _, _ in owners]}): a second (decoy) step makes the "
            "one that actually runs unidentifiable"
        ]
    job_name, job, step = owners[0]

    # Whitespace is collapsed because unquoted spacing is not semantic — but a second LINE is: a
    # newline is a command separator, not a space, and a `\`-continuation is how a script gets
    # wrapped around the command. (The previous `\`+newline replacement could never fire: a shell
    # continuation always has a space before the backslash, so the result always kept two spaces.)
    run_lines = [line for line in _code(step).splitlines() if line.strip()]
    run = " ".join(_code(step).split())
    if len(run_lines) > 1:
        violations.append(
            f"the gate step's `run:` must be a SINGLE line (got {len(run_lines)}): a "
            "`\\`-continuation or a multi-line script puts shell operators around the command, "
            "and a newline is a command separator rather than a space"
        )
    elif run != ORDERING_HALF_INVOCATION:
        violations.append(
            f"the gate step must invoke EXACTLY `{ORDERING_HALF_INVOCATION}` — no shell operator "
            f"and no trailing anything, or a real failure reports green; got {run!r}"
        )
    # `if:` is excluded here and judged by `_ALWAYS_RUN` below: the allow-list forbids the key, so
    # folding it in would make that check unreachable — and would red an `always()` guard, which
    # does not drop enforcement, with a message claiming it does.
    extra_keys = sorted(str(k) for k in set(step) - _SAFE_STEP_KEYS - {"if"})
    if extra_keys:
        violations.append(
            f"the gate step may carry only {sorted(_SAFE_STEP_KEYS)}; found {extra_keys} — every "
            "other Actions step key can skip the step or mask its exit code"
        )
    if step.get("if", "always()") not in _ALWAYS_RUN:
        violations.append(
            f"the gate step must be unconditional; got if: {step.get('if')!r}"
        )

    for scope, label, allowed_env in (
        (workflow, "python-ci.yml", _SAFE_WORKFLOW_ENV),
        (job, f"the `{job_name}` job", _SAFE_JOB_ENV.get(job_name, frozenset())),
    ):
        for key in ("shell", "working-directory"):
            value = ((scope.get("defaults") or {}).get("run") or {}).get(key)
            if value is not None:
                violations.append(
                    f"`defaults.run.{key}` on {label} ({value!r}) can swallow the gate's exit "
                    "code or run it against a stub tree"
                )
        extra_env = sorted(set(scope.get("env") or {}) - allowed_env)
        if extra_env:
            violations.append(
                f"`env:` on {label} carries {extra_env}, and any key there can hook the step's "
                "interpreter or shell (`BASH_ENV`, a `PYTHONPATH` `sitecustomize.py`, "
                "`LD_PRELOAD`, `PATH`) so `python3` exits 0 with the real check never running; "
                f"only {sorted(allowed_env)} are permitted"
            )
    if "if" in job:
        violations.append(
            f"the `{job_name}` job must be unconditional — `if:` on the job skips the gate "
            "entirely (a skipped leg certifies nothing)"
        )
    if job.get("continue-on-error"):
        violations.append(f"`continue-on-error` on the `{job_name}` job silences the gate")
    if "container" in job:
        violations.append(
            f"a `container:` on the `{job_name}` job replaces the image the gate runs in"
        )

    shims = sorted(
        str(s.get("name") or s.get("uses") or "<unnamed>")
        for s in job.get("steps") or []
        if s is not step
        and ("GITHUB_PATH" in _code(s) or "GITHUB_ENV" in _code(s))
    )
    if shims:
        violations.append(
            f"a sibling step writes `$GITHUB_PATH`/`$GITHUB_ENV` ({shims}) — appending a shim "
            "directory makes `python3` resolve to a stub that exits 0"
        )

    expected_steps = _EXPECTED_JOB_STEPS.get(job_name)
    if expected_steps is None:
        violations.append(
            f"the `{job_name}` job owns the gate step but declares no step allow-list in "
            f"{sorted(_EXPECTED_JOB_STEPS)} — a sibling step in an undeclared job can rewrite the "
            "tool or restore the evidence tree from another ref, leaving the gate step's own "
            "shape clean"
        )
    else:
        step_set = _step_set_violation(job, f"the `{job_name}` job", expected_steps, sentinel=step)
        if step_set:
            violations.append(step_set)

    checkouts = [
        s for s in job.get("steps") or [] if str(s.get("uses", "")).startswith("actions/checkout")
    ]
    if not checkouts:
        violations.append(f"the `{job_name}` job must check out the repo")
    for checkout in checkouts:
        depth = (checkout.get("with") or {}).get("fetch-depth")
        if depth != 0:
            violations.append(
                f"the `{job_name}` job's checkout must use `fetch-depth: 0` (got {depth!r}): the "
                "ordering half reads add-commit epochs, and a shallow clone only reports "
                "UNPROVABLE"
            )

    if job.get("runs-on") != _EXPECTED_RUNS_ON:
        violations.append(
            f"the `{job_name}` job's `runs-on:` must stay `{_EXPECTED_RUNS_ON}` (got "
            f"{job.get('runs-on')!r}): a runner whose `python3` is a stub exits 0 with the real "
            "check never running — the same knob `container:` is refused for"
        )

    gate_name = "python-ci-gate"
    gate_job = jobs.get(gate_name) or {}
    if job_name not in (gate_job.get("needs") or []):
        violations.append(
            f"the `{job_name}` job must be in `{gate_name}.needs`, or the gate can red without "
            "blocking the merge"
        )

    # The SINK. `needs` membership says a red leg FEEDS the aggregate; it says nothing about the
    # aggregate still being able to report it — and the aggregate is the ONLY `merge_condition`
    # (`.mergify.yml`; it is one of 7 contexts in `docs/ci/required-contexts.json`), so silencing it
    # turns a red ordering half into a merge-green PR with every producer-side check still green.
    if gate_job.get("if") not in _ALWAYS_RUN:
        violations.append(
            f"the `{gate_name}` job must be `if: always()` (got {gate_job.get('if')!r}): on the "
            "default `success()` it is skipped whenever a leg fails, so the only required check "
            "stops reporting the failure"
        )
    if gate_job.get("runs-on") != _EXPECTED_RUNS_ON:
        violations.append(
            f"the `{gate_name}` job's `runs-on:` must stay `{_EXPECTED_RUNS_ON}` (got "
            f"{gate_job.get('runs-on')!r}): a runner whose shell or `python3` is a stub exits 0 "
            "and the required check certifies a red leg"
        )
    for key in ("shell", "working-directory"):
        value = ((gate_job.get("defaults") or {}).get("run") or {}).get(key)
        if value is not None:
            violations.append(
                f"`defaults.run.{key}` on the `{gate_name}` job ({value!r}) can swallow the "
                "aggregate's exit code, so a red leg never reaches the required check"
            )
    if set(gate_job.get("env") or {}):
        violations.append(
            f"`env:` on the `{gate_name}` job carries {sorted(gate_job.get('env') or {})}: a "
            "`BASH_ENV`/`PYTHONPATH`/`LD_PRELOAD` hook makes the aggregate exit 0 and the required "
            "check certifies a red leg"
        )
    if gate_job.get("continue-on-error"):
        violations.append(
            f"`continue-on-error` on the `{gate_name}` job silences the required check"
        )
    if "container" in gate_job:
        violations.append(
            f"a `container:` on the `{gate_name}` job replaces the image the aggregate runs in"
        )
    # The sink's STEP LIST is a scope the per-step key allow-list below cannot see: a step APPENDED
    # to the job carries only `name`/`run`, and its `run:` can write `BASH_ENV=` into `$GITHUB_ENV`
    # (or a stub directory into `$GITHUB_PATH`) for the aggregate step, which is non-interactive
    # bash and therefore sources `$BASH_ENV` before its script. A membership test over the
    # aggregate's script is blind in the same way: the whole failure sweep can be replaced by an
    # `echo` that still contains the marker words. So the aggregate's steps are pinned by whole
    # definition, exactly as the gate's owner job is (`_step_set_violation`).
    sink_step_set = _step_set_violation(
        gate_job, f"the `{gate_name}` job", _EXPECTED_JOB_STEPS[gate_name])
    if sink_step_set:
        violations.append(sink_step_set)
    sink_shims = sorted(
        str(s.get("name") or s.get("uses") or "<unnamed>")
        for s in gate_job.get("steps") or []
        if "GITHUB_PATH" in _code(s) or "GITHUB_ENV" in _code(s)
    )
    if sink_shims:
        violations.append(
            f"a step on `{gate_name}` writes `$GITHUB_PATH`/`$GITHUB_ENV` ({sink_shims}) — a "
            "`BASH_ENV`/`PATH` export from a preceding step hooks the aggregate itself, whose "
            "`run:` is non-interactive bash, so it exits 0 on a red leg and the only "
            "`merge_condition` certifies the merge"
        )
    # The sink's STEPS are a scope of their own, and the one the aggregate's own `env:` refusal
    # above cannot see: `env:` is a per-step key too, and the aggregate's `run:` is non-interactive
    # bash, which sources `$BASH_ENV` before the script. So a step-level hook exits 0 with a red leg
    # and the required check certifies the merge — the exact outcome this block exists to prevent.
    # Same ALLOW-list shape as the gate step above (`_SAFE_STEP_KEYS`), not an enumeration of the
    # silencing keys: `continue-on-error:`, `shell:`, `working-directory:` and `env:` each do it.
    for aggregate_step in gate_job.get("steps") or []:
        step_label = str(aggregate_step.get("name") or aggregate_step.get("uses") or "<unnamed>")
        # `if:` is judged by `_ALWAYS_RUN` instead (as on the gate step): the allow-list forbids the
        # key, so folding it in would make that check unreachable and would red `if: always()`,
        # which does not drop enforcement.
        aggregate_extra = sorted(
            str(k) for k in set(aggregate_step) - _SAFE_STEP_KEYS - {"if"}
        )
        if aggregate_extra:
            violations.append(
                f"the `{gate_name}` step `{step_label}` may carry only "
                f"{sorted(_SAFE_STEP_KEYS)}; found {aggregate_extra} — `env:` here is the same "
                "hook the job-level `env:` above is refused for, one scope down: a `BASH_ENV`/"
                "`PYTHONPATH`/`LD_PRELOAD`/`PATH` entry makes the aggregate exit 0 and the "
                "required check certifies a red leg"
            )
        if aggregate_step.get("if", "always()") not in _ALWAYS_RUN:
            violations.append(
                f"the `{gate_name}` step `{step_label}` must be unconditional; got "
                f"if: {aggregate_step.get('if')!r} — a skipped aggregate step leaves the only "
                "required check green"
            )
    aggregate = " ".join(_code(s) for s in gate_job.get("steps") or [])
    missing = [marker for marker in _EXPECTED_AGGREGATE_MARKERS if marker not in aggregate]
    if missing:
        violations.append(
            f"the `{gate_name}` steps no longer carry {missing}: they are what turns a failed need "
            "into a red required check, and without them a red leg merges green"
        )
    return violations


def test_ci_runs_the_ordering_half_on_full_history():
    """REGRESSION (#9075 review r6 P1, sharpened by the final review's ISSUE 1): no automated
    path invoked the gate's git half — and the pin that fixed that could not see a silenced step.

    CI test jobs check out at `fetch-depth: 1`, so the ordering half reports the third outcome
    there and the git-dependent bounds (a)/(b) never evaluate. The full-history verdict therefore
    lives in the `changes` job — the one checkout with `fetch-depth: 0` — so a workflow edit must
    not be able to (a) drop the step, (b) move it to a shallow job, or (c) leave it in place while
    neutralising its exit code. The pre-fix pin asserted a membership test over the joined `run:`
    scalars, which `|| true`, `continue-on-error: true`, `if: false`, a `shell:` override or a
    `PATH` shim all satisfy.
    """
    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    assert ci_ordering_half_violations(workflow) == []


#: Per-label substrings the violation MESSAGE must carry. A branch that starts reporting some
#: other defect — or stops naming the idiom it exists to refuse — stays red instead of quietly
#: passing on "some violation".
_EXPECTED_MESSAGE = {
    "a sibling `$GITHUB_PATH` writer": "$GITHUB_PATH",
    "a sibling `$GITHUB_ENV` writer": "$GITHUB_ENV",
    "a job-level `continue-on-error: true`": "continue-on-error",
    "a job-level `container:`": "container:",
    "a workflow-level PATH shim": "on python-ci.yml",
    "a job-level `defaults.run.working-directory`": "defaults.run.working-directory",
    # `defaults.run.*` at WORKFLOW scope, not just on the job: same hook, one scope up.
    "a workflow-level `defaults.run.shell`": "defaults.run.shell` on python-ci.yml",
    "a workflow-level `defaults.run.working-directory`": "defaults.run.working-directory` on python-ci.yml",
    # The `env:` KEY allow-list, not just a `PATH` test: every one of these is the same hook
    # under the next key over, and each must be refused by the key it names.
    "a workflow-level `BASH_ENV` hook": "BASH_ENV",
    "a workflow-level `PYTHONPATH` hook": "PYTHONPATH",
    "a job-level `LD_PRELOAD` hook": "LD_PRELOAD",
    # The step-SET allow-list: a sibling step neutralises the gate without touching the gate step.
    "a sibling stub `python3` writer": "added [",
    "a sibling that restores the evidence tree": "added [",
    "a sibling rewritten in place": "rewritten [",
    # A step added UNDER an existing step's name: the count is what catches it, not the label set.
    "a sibling added under an existing label": "added [",
    # The gate step's own `run:` must be one line.
    "a `\\`-continuation in the gate step": "SINGLE line",
    # The runner is the same knob `container:` is refused for.
    "a `runs-on:` swap on the gate's job": "runs-on",
    "a `runs-on:` swap on the required aggregate": "runs-on",
    # The SINK: the aggregate is the only `merge_condition`, so it must be able to report a red leg.
    # Its own knobs are the highest-stakes branches here: a skipped or `continue-on-error`'d
    # aggregate certifies nothing.
    "an `if: success()` on the required aggregate": "must be `if: always()`",
    "a `continue-on-error: true` on the required aggregate": "continue-on-error",
    "a `container:` on the required aggregate": "replaces the image",
    # The sink's STEP LIST and its shim scan: a step APPENDED to the aggregate job exports
    # `BASH_ENV`/`PATH` for the aggregate step, and a membership test cannot see the sweep replaced
    # by an `echo` that still carries the marker words.
    "a `$GITHUB_ENV` writer step on the aggregate": "$GITHUB_ENV",
    "a `$GITHUB_PATH` writer step on the aggregate": "$GITHUB_PATH",
    "an aggregate step that writes `$GITHUB_ENV` in place": "$GITHUB_ENV",
    "an aggregate step replaced by marker-carrying `echo`s": "rewritten [",
    "an aggregate `defaults.run.shell`": "defaults.run.shell",
    "an aggregate `env:` hook": "BASH_ENV",
    # The sink's STEPS are a separate scope: the job-level `env:` refusal above cannot see these.
    "a step-level `env:` hook on the aggregate": "BASH_ENV",
    "a step-level `continue-on-error: true` on the aggregate": "continue-on-error",
    "a step-level `if: false` on the aggregate": "unconditional",
    "an aggregate stripped of the failure sweep": "no longer carry",
    "a silenced gate step hidden behind a decoy": "exactly ONE step",
}


def _mutations():
    """(label, mutate) pairs — each leaves the command in place and silences it.

    "Silences" is the premise the parametrized test asserts (`_loose_pin_holds` green), so a case
    that merely makes the gate AMBIGUOUS belongs with a silencing edit alongside it — the decoy
    case below pairs a decoy with a `|| true` for exactly that reason: a duplicate with the real
    step intact still enforces, so it would not prove the pre-fix hole it claims.
    """

    def gate_step(workflow):
        return next(
            s for s in workflow["jobs"]["changes"]["steps"]
            if "tools/capstone_preregistration_check.py" in (s.get("run") or "")
        )

    def silenced_gate_behind_a_decoy(workflow):
        step = gate_step(workflow)
        step["run"] = step["run"] + " || true"
        workflow["jobs"]["changes"]["steps"].append(
            {"name": "decoy", "run": ORDERING_HALF_INVOCATION})

    def rewritten_sibling(workflow):
        """Same name, same first `run:` token, different script."""
        next(
            s for s in workflow["jobs"]["changes"]["steps"]
            if str(s.get("name", "")).startswith("Secrets scan")
        )["run"] = (
            "if [ -d docs/evidence/7949-capstone ]; then git checkout origin/main -- "
            "docs/evidence/7949-capstone; fi"
        )

    return {
        "a trailing `|| true`": lambda w: gate_step(w).__setitem__(
            "run", gate_step(w)["run"] + " || true"),
        "a `; exit 0` tail": lambda w: gate_step(w).__setitem__(
            "run", gate_step(w)["run"] + "; exit 0"),
        "`continue-on-error: true`": lambda w: gate_step(w).__setitem__(
            "continue-on-error", True),
        "`if: false`": lambda w: gate_step(w).__setitem__("if", "false"),
        "a `shell:` override": lambda w: gate_step(w).__setitem__("shell", "bash -c 'true'"),
        "a `working-directory:`": lambda w: gate_step(w).__setitem__(
            "working-directory", "tools"),
        "a step-level PATH shim": lambda w: gate_step(w).__setitem__(
            "env", {"PATH": "/tmp/shim:/usr/bin"}),
        "a job-level PATH shim": lambda w: w["jobs"]["changes"].__setitem__(
            "env", {"PATH": "/tmp/shim:/usr/bin"}),
        "a job-level `defaults.run.shell`": lambda w: w["jobs"]["changes"].__setitem__(
            "defaults", {"run": {"shell": "bash -c 'true'"}}),
        "a job-level `defaults.run.working-directory`": lambda w: w["jobs"][
            "changes"].__setitem__("defaults", {"run": {"working-directory": "tools"}}),
        "a workflow-level PATH shim": lambda w: w.setdefault("env", {}).__setitem__(
            "PATH", "/tmp/shim:/usr/bin"),
        # Same hook, other keys — the `PATH`-only test could not see any of them.
        "a workflow-level `BASH_ENV` hook": lambda w: w.setdefault("env", {}).__setitem__(
            "BASH_ENV", ".ci-hooks/env.sh"),
        "a workflow-level `PYTHONPATH` hook": lambda w: w.setdefault("env", {}).__setitem__(
            "PYTHONPATH", ".ci-hooks"),
        "a job-level `LD_PRELOAD` hook": lambda w: w["jobs"]["changes"].__setitem__(
            "env", {"LD_PRELOAD": "/tmp/hook.so"}),
        "a job-level `continue-on-error: true`": lambda w: w["jobs"]["changes"].__setitem__(
            "continue-on-error", True),
        "a job-level `container:`": lambda w: w["jobs"]["changes"].__setitem__(
            "container", "stub:latest"),
        "a job-level `if:`": lambda w: w["jobs"]["changes"].__setitem__("if", "false"),
        "a silenced gate step hidden behind a decoy": silenced_gate_behind_a_decoy,
        "a sibling `$GITHUB_PATH` writer": lambda w: w["jobs"]["changes"]["steps"].insert(
            0, {"name": "shim", "run": "echo /tmp/shim >> $GITHUB_PATH"}),
        "a sibling `$GITHUB_ENV` writer": lambda w: w["jobs"]["changes"]["steps"].append(
            {"name": "shim", "run": "echo PATH=/tmp/shim >> $GITHUB_ENV"}),
        # Siblings that never mention `$GITHUB_PATH`/`$GITHUB_ENV` and leave the gate step
        # byte-identical: only the step-SET allow-list can see them.
        "a sibling stub `python3` writer": lambda w: w["jobs"]["changes"]["steps"].insert(
            6, {"name": "Normalise tooling", "run": (
                "printf '#!/bin/sh\\nexit 0\\n' > /usr/local/bin/python3"
                " && chmod +x /usr/local/bin/python3")}),
        "a sibling that restores the evidence tree": lambda w: w["jobs"][
            "changes"]["steps"].insert(6, {
                "name": "Restore baseline evidence",
                "run": "git checkout origin/main -- docs/evidence/7949-capstone"}),
        "a sibling added under an existing label": lambda w: w["jobs"]["changes"][
            "steps"].insert(2, {
                "name": "Install pyyaml",
                "run": "printf '#!/bin/sh\\nexit 0\\n' > /usr/local/bin/python3"}),
        "a `\\`-continuation in the gate step": lambda w: gate_step(w).__setitem__(
            "run", "python3 tools/capstone_preregistration_check.py \\\n"
                   "    --dir docs/evidence/7949-capstone --no-corpus"),
        "a `runs-on:` swap on the gate's job": lambda w: w["jobs"]["changes"].__setitem__(
            "runs-on", "self-hosted"),
        "a `runs-on:` swap on the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"].__setitem__("runs-on", "self-hosted"),
        "an aggregate `defaults.run.shell`": lambda w: w["jobs"]["python-ci-gate"].__setitem__(
            "defaults", {"run": {"shell": "bash -c 'true'"}}),
        "an aggregate `env:` hook": lambda w: w["jobs"]["python-ci-gate"].__setitem__(
            "env", {"BASH_ENV": ".ci/hook.sh"}),
        # …and the same hook one scope DOWN, on the aggregate's own step. The aggregate's `run:` is
        # non-interactive bash, so `$BASH_ENV` is sourced before the script: a step-level `env:`
        # that `exit 0`s leaves the only REQUIRED check green while the ordering half is red.
        "a step-level `env:` hook on the aggregate": lambda w: w["jobs"]["python-ci-gate"][
            "steps"][0].__setitem__("env", {"BASH_ENV": ".ci/hook.sh"}),
        "a step-level `continue-on-error: true` on the aggregate": lambda w: w["jobs"][
            "python-ci-gate"]["steps"][0].__setitem__("continue-on-error", True),
        "a step-level `if: false` on the aggregate": lambda w: w["jobs"]["python-ci-gate"][
            "steps"][0].__setitem__("if", "false"),
        "an aggregate stripped of the failure sweep": lambda w: w["jobs"]["python-ci-gate"][
            "steps"].__setitem__(0, {"name": "Aggregate matrix result", "run": "echo ok"}),
        # The sink's STEP LIST — the one scope the aggregate's per-step key allow-list cannot see.
        # A step appended to the job carries only `name`/`run`, and its `run:` exports `BASH_ENV`
        # (or a shim directory) for the aggregate step, which is non-interactive bash.
        "a `$GITHUB_ENV` writer step on the aggregate": lambda w: w["jobs"][
            "python-ci-gate"]["steps"].insert(0, {
                "name": "Prepare env",
                "run": 'echo "BASH_ENV=.ci/hook.sh" >> $GITHUB_ENV'}),
        "a `$GITHUB_PATH` writer step on the aggregate": lambda w: w["jobs"][
            "python-ci-gate"]["steps"].append({
                "name": "Prepare path", "run": "echo /tmp/shim >> $GITHUB_PATH"}),
        # …and the same write hidden INSIDE the declared step, where the step set is unchanged.
        "an aggregate step that writes `$GITHUB_ENV` in place": lambda w: w["jobs"][
            "python-ci-gate"]["steps"][0].__setitem__(
                "run", w["jobs"]["python-ci-gate"]["steps"][0]["run"]
                + '\necho "BASH_ENV=.ci/hook.sh" >> $GITHUB_ENV'),
        # Membership vs whole definition: the marker words survive, the sweep does not. Every
        # marker test in the pin was green on this before the step list was digest-pinned.
        "an aggregate step replaced by marker-carrying `echo`s": lambda w: w["jobs"][
            "python-ci-gate"]["steps"].__setitem__(
                0, {"name": "Aggregate matrix result",
                    "run": "echo 'needs.*.result failure'"}),
        # The sink's own job-level knobs: each of them certifies nothing while it is set.
        "an `if: success()` on the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"].__setitem__("if", "success()"),
        "a `continue-on-error: true` on the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"].__setitem__("continue-on-error", True),
        "a `container:` on the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"].__setitem__("container", "stub:latest"),
        # `defaults.run.*` one scope ABOVE the job-level cases: same hook, same exit code.
        "a workflow-level `defaults.run.shell`": lambda w: w.setdefault(
            "defaults", {}).__setitem__("run", {"shell": "bash -c 'true'"}),
        "a workflow-level `defaults.run.working-directory`": lambda w: w.setdefault(
            "defaults", {}).__setitem__("run", {"working-directory": "tools"}),
        # Same name, same first `run:` token, different script — the case a name+token signature
        # could not see (`_step_digest` pins the sibling's whole definition instead).
        "a sibling rewritten in place": rewritten_sibling,
        "dropping it from the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"]["needs"].remove("changes"),
    }


@pytest.mark.parametrize("label", list(_mutations()))
def test_the_ci_pin_rejects_every_silencing_idiom(label):
    """The failing direction for the pin above: each mutation is invisible to the PRE-FIX pin.

    Every case keeps the gate's command string present, so the old membership assertion stayed
    green while the check could never fail a PR; the strict pin must name the violation.

    Every branch of the pin that can leave the gate GREEN has a case here — including, one scope
    out, the SINK's: a step appended to `python-ci-gate` that exports `BASH_ENV`/`PATH` through
    `$GITHUB_ENV`/`$GITHUB_PATH`, its aggregate step rewritten into a marker-carrying `echo`, and
    each of the aggregate job's own knobs (`if:`, `continue-on-error:`, `container:`) — plus
    `defaults.run.*` at WORKFLOW scope. The one branch without a case — "the job must check out the
    repo" — is the opposite shape: deleting the `changes` checkout reds the job instead of greening
    the gate, and the PRE-FIX pin already refused it, the same way it already refused a shallow
    checkout (`test_the_ci_pin_still_catches_a_shallow_checkout`), so claiming it here as a pre-fix
    hole would overstate the gap.

    The `if: always()` allow-list has its own positive case
    (`test_the_ci_pin_allows_an_always_guard_on_the_gate_step`) so it cannot become dead code
    again.
    """
    import copy

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    mutated = copy.deepcopy(workflow)
    _mutations()[label](mutated)
    assert _loose_pin_holds(mutated), (
        f"`{label}` must leave the PRE-FIX pin green, or this case proves nothing"
    )
    violations = ci_ordering_half_violations(mutated)
    assert violations, f"`{label}` silences the gate and the strict pin missed it"
    needle = _EXPECTED_MESSAGE.get(label)
    if needle:
        assert needle in " ".join(violations), (
            f"`{label}` must be refused BY NAME (expected {needle!r} in the message); got "
            f"{violations}"
        )
    assert ci_ordering_half_violations(workflow) == [], "the unmutated workflow must stay clean"


def test_the_ci_pin_allows_an_always_guard_on_the_gate_step():
    """`if: always()` is the ONE `if:` form that does not drop enforcement.

    Without this case the two branches disagree: the step-key allow-list would refuse every `if:`,
    leaving the `_ALWAYS_RUN` check unreachable and its message claiming a guard is a silencing
    idiom. It matters in practice — the `changes` job's earlier steps can fail, and a maintainer
    adding the guard must not get a false red that tells them the opposite of the rule.
    """
    import copy

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    for form in _ALWAYS_RUN:
        mutated = copy.deepcopy(workflow)
        next(
            s for s in mutated["jobs"]["changes"]["steps"]
            if "tools/capstone_preregistration_check.py" in (s.get("run") or "")
        )["if"] = form
        violations = ci_ordering_half_violations(mutated)
        assert violations == [], (
            f"`if: {form}` runs unconditionally and must be allowed; got {violations}"
        )


def test_the_ci_pin_refuses_a_reordered_step_set():
    """A pure reorder keeps every step's shape identical, so only the ORDER half can see it.

    Not a `_mutations()` case: moving a step does not by itself silence the gate, so it could not
    honestly claim the pre-fix hole the parametrized cases assert. It must still be refused — a
    step moved above the checkout or above the gate can rewrite the tree the gate evaluates — and
    the message must name the order rather than report an empty unexpected/missing pair.
    """
    import copy

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    mutated = copy.deepcopy(workflow)
    steps = mutated["jobs"]["changes"]["steps"]
    gate_index = next(
        i for i, s in enumerate(steps)
        if "tools/capstone_preregistration_check.py" in (s.get("run") or "")
    )
    steps.insert(0, steps.pop(gate_index))
    violations = ci_ordering_half_violations(mutated)
    assert "out of order" in " ".join(violations), (
        f"a reordered step list must be refused by name, not as an empty diff; got {violations}"
    )


def test_the_ci_pin_refuses_an_undeclared_owner_job():
    """Moving the gate to a peer job is allowed in principle, but not silently: the pin locates the
    step by content, so the new owner must DECLARE its step allow-list (`_EXPECTED_JOB_STEPS`).

    An undeclared owner is a violation, never a pass — otherwise relocating the gate would drop
    the sibling-step protection, which is the whole point of pinning the step set.
    """
    import copy

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    mutated = copy.deepcopy(workflow)
    gate_step = next(
        s for s in mutated["jobs"]["changes"]["steps"]
        if "tools/capstone_preregistration_check.py" in (s.get("run") or "")
    )
    mutated["jobs"]["changes"]["steps"].remove(gate_step)
    mutated["jobs"]["manifest-integrity"]["steps"].append(gate_step)
    violations = ci_ordering_half_violations(mutated)
    assert "declares no step allow-list" in " ".join(violations), (
        f"an undeclared owner job must be refused by name (moving the gate is a deliberate pin "
        f"edit, not a silent one); got {violations}"
    )


def test_the_ci_pin_still_catches_a_shallow_checkout():
    """The one idiom the PRE-FIX pin already refused — modelled honestly, not as a new gap.

    `origin/main`'s pin asserted `fetch-depth == 0` on every checkout, so this mutation was RED
    before the rewrite. Keeping it inside `_mutations()` (whose every case asserts the old pin was
    green) would have claimed a hole that never existed; the strict pin must still catch it, and
    say so by name.
    """
    import copy

    import yaml

    workflow = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "python-ci.yml").read_text())
    mutated = copy.deepcopy(workflow)
    next(
        s for s in mutated["jobs"]["changes"]["steps"]
        if str(s.get("uses", "")).startswith("actions/checkout")
    ).__setitem__("with", {"fetch-depth": 1})
    assert not _loose_pin_holds(mutated), (
        "a shallow checkout was ALREADY caught by the pre-fix pin (`fetch-depth == 0`); if the "
        "pre-fix model says otherwise it no longer models `origin/main`"
    )
    violations = ci_ordering_half_violations(mutated)
    assert "fetch-depth" in " ".join(violations), (
        f"the strict pin must refuse a shallow checkout by name; got {violations}"
    )


# ── the content-based arm: a log that PROVES it measured nothing (#9089) ─────


def _capstone_body(*, run_status="REFUSED", prereg_status="ABSENT",
                   numerator=None, denominator=None, schema=None, blocks=1,
                   receipt=None) -> str:
    """A capstone log whose machine record is the only thing the exemption may read."""
    from tools import capstone_verdict_check as cvc

    run = {"status": run_status, "steps_observed": [], "steps_required": ["S0"]}
    if receipt is not None:
        # `run.e4_receipt` is where the probe writes the E4 number on the COMPLETED path.
        run["e4_receipt"] = receipt
    record = {
        # The REAL schema, imported: the exemption pins it, so a fixture with an invented one would
        # test a shape the gate refuses (#9089 review P2).
        "schema": schema if schema is not None else cvc.SCHEMA,
        "prereg": {"status": prereg_status, "sha": "0" * 40},
        "run": run,
        "rows": [{"criterion": "E1", "verdict": "BLOCKED", "numerator": numerator,
                  "denominator": denominator, "blocked_by": "#9075"}],
    }
    block = ("=== CAPSTONE-VERDICT-BEGIN ===\n"
             f"{json.dumps(record, indent=2, sort_keys=True)}\n"
             "=== CAPSTONE-VERDICT-END ===\n")
    return "# capstone\n" + block * blocks


def test_a_log_that_proves_it_measured_nothing_needs_no_ordering(tmp_path, monkeypatch):
    """THE FIX (#9089): history put the log first, and the log carries NO number at all.

    `capstone.log` is added before `PRE-REGISTRATION.md` because it was written before the repo
    required a registration (#9507). The ordering rule protects a NUMBER from thresholds written
    afterwards; a log whose own machine record says `prereg.status=ABSENT`, `run.status=REFUSED`
    and `numerator/denominator: null` on every row has no number to launder. It is exempt on its
    CONTENT, re-asked every run — not by pinning the file name.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "capstone.log", epoch=1_700_000_000, body=_capstone_body())
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log]) == []


@pytest.mark.parametrize("overrides", [
    {"numerator": 0, "denominator": 5},
    {"run_status": "COMPLETED"},
    # #9089 review P1: the measurement test used to read only `rows`.
    {"receipt": {"numerator": 14, "denominator": 15}},
    # #9089 review P2: the receipt's counts are not all named numerator/denominator — the
    # probe writes `s4_items`, `merged_items`, `corrected_by_s4`, `verbatim_reemissions` and
    # `added_by_s4` too, so a two-key match exempted a record carrying five other numbers.
    {"receipt": {"s4_items": 200, "merged_items": 7}},
])
def test_the_content_arm_stops_covering_the_log_the_moment_it_could_score(
        tmp_path, monkeypatch, overrides):
    """Self-healing, and each of the two measurement conditions is load-bearing on its own.

    A row that gains a number, or a run that COMPLETED, means the log could have been scored
    against thresholds written after it — so the exemption must stop applying with no second edit
    anywhere.

    The PREREG STATUS is deliberately NOT in this list (#9089 review cycle 4, measured): the
    bundle's own documented re-run passes G0 and refuses at G1, emitting `prereg.status=OK` with
    every number null — a log that proves it measured nothing. Requiring `ABSENT` refused to
    exempt it, which turned that documented command into an ordering violation. What the run
    READ cannot make laundering possible; what it MEASURED can, and that is what these cases are.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "capstone.log", epoch=1_700_000_000,
                     body=_capstone_body(**overrides))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    # THE RULED default (an epoch comparison on the base) and THE PROPOSAL (topology) both refuse
    # this tree, and both say the log does not prove it measured nothing.
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1, failures
    assert "not strictly after" in failures[0], failures
    assert "does not prove it measured nothing" in failures[0], failures
    failures = _armed(prereg, [log])
    assert len(failures) == 1, failures
    assert "is an ANCESTOR of the pre-registration's add-commit" in failures[0], failures
    assert "does not prove it measured nothing" in failures[0], failures


def test_a_post_g0_refusal_with_no_number_is_exempt(tmp_path, monkeypatch):
    """#9089 review cycle 4 — the bundle's OWN documented re-run must not turn CI red.

    Measured at this head: `capstone_run.py --prereg <sha> --corpus battery/config/corpus.json`
    passes G0 (the registration is committed) and refuses at G1 (no frozen corpus sha), so it
    emits `prereg.status=OK`, `run.status=REFUSED` and every numerator/denominator null. An
    earlier revision of the predicate exempted only `ABSENT` and therefore refused this log —
    which, once committed by the README's own `| tee`, reds the ordering check for every PR on
    a log that provably measured nothing.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "capstone.log", epoch=1_700_000_000,
                     body=_capstone_body(prereg_status="OK"))
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    assert check.check_ordering(prereg, [log]) == []


def test_the_content_arm_refuses_to_exempt_anything_it_cannot_read(tmp_path):
    """Fail-closed: a missing/!JSON/rowless/ambiguous record is a REFUSAL, never a pass.

    The last two arms are the ones a reviewer measured (#9089 review P1/P2): a log carrying TWO
    verdict blocks (reachable by `capstone_run.py | tee -a capstone.log`) could hide a number in
    the block the reader did not take, and an unrecognised schema is a shape this predicate cannot
    read — so both refuse rather than reading the shape they happen to understand.
    """
    for body in (
        "no machine record here\n",
        "=== CAPSTONE-VERDICT-BEGIN ===\n{not json\n=== CAPSTONE-VERDICT-END ===\n",
        "=== CAPSTONE-VERDICT-BEGIN ===\n{}\n=== CAPSTONE-VERDICT-END ===\n",
        _capstone_body().replace('"status": "REFUSED"', '"status": ""'),
        _capstone_body(blocks=2),
        _capstone_body(schema="capstone-verdict-v2"),
        # #9089 review P2: a document nested past the parser's limit raises RecursionError,
        # which is NOT a JSONDecodeError — it escaped this predicate entirely.
        "=== CAPSTONE-VERDICT-BEGIN ===\n" + "[" * 100000 + "\n=== CAPSTONE-VERDICT-END ===\n",
    ):
        path = tmp_path / "_probe_body.log"
        path.write_text(body)
        try:
            ok, why = check._log_declares_no_measurement(path)
        finally:
            path.unlink()
        assert not ok and why, body


def test_the_shipped_capstone_log_proves_it_measured_nothing():
    """The real artefact, on the real file — this is what unblocks the required check."""
    ok, why = check._log_declares_no_measurement(PREREG_DIR / "capstone.log")
    assert ok, why


def test_an_exempted_log_is_named_in_the_report_not_waived_silently(tmp_path, monkeypatch):
    """A waived ordering violation must be VISIBLE (#9089 review P2).

    The tool's own rule, ninety lines from the exemption: a system that reads as fully checked
    while one half never ran is the silent-green shape this tool exists to refuse. An exempted log
    makes the ordering line GREEN, so the name has to travel with it — otherwise the next lane
    cannot tell an ordered log from a waived one.
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "capstone.log", epoch=1_700_000_000, body=_capstone_body())
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    exempted: list[str] = []
    assert check.check_ordering(prereg, [log], None, exempted) == []
    assert exempted == ["capstone.log"], exempted


# ── the registry may not contradict the history or the model (#9736, #9738) ──


def _record_copy(tmp_path: Path, drop: tuple[str, ...] = (), **patches) -> Path:
    """A throwaway evidence directory: the REAL document + a MUTATED copy of the real record."""
    (tmp_path / check.PREREG_NAME).write_text(PREREG.read_text())
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    rec.update(patches)
    for key in drop:
        rec.pop(key, None)
    (tmp_path / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    return tmp_path


def _crit(rec: dict, cid: str) -> dict:
    return next(c for c in rec["criteria"] if c["id"] == cid)


def _contradictions(directory: Path, scopes: list[str] | None = None) -> list[str]:
    text = (directory / check.PREREG_NAME).read_text()
    return check.check_registry_contradictions(
        directory, check.parse_criteria_rows(text), check.parse_pins(text), scopes
    )


def test_the_real_record_discloses_every_visible_measurement():
    """The committed pair, held to itself: every `true` is cited and every citation is `true`."""
    assert _contradictions(PREREG_DIR) == []
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    assert _crit(rec, "E5")["measurement_visible_to_author"] is True
    assert rec["prior_measurements"]["E5"]["commit"] == "ecc7bc8ca"
    # E3's measurement POSTDATES the registration, so the field is honestly false and carries no
    # `prior_measurements` entry — the distinction the two fields exist to keep apart.
    assert _crit(rec, "E3")["measurement_visible_to_author"] is False
    assert "E3" not in rec["prior_measurements"]
    assert _crit(rec, "E3")["post_registration_measurement"]
    # E4's `false` is RIGHT and, until #10525's review, unexplained — the shape that made it
    # indistinguishable from a stale one. It stays false (its receipt ran 2026-10-19, before the
    # record, but landed at `d50217273` on 2026-10-22, AFTER it, so nothing measured by the row was
    # in the tree its author wrote), and it now has to say so.
    e4 = _crit(rec, "E4")
    assert e4["measurement_visible_to_author"] is False
    assert "E4" not in rec["prior_measurements"]
    basis = e4["measurement_visible_to_author_basis"]
    assert "2026-10-19" in basis and "d50217273" in basis, basis
    assert "a6085af84" in basis, basis


def test_a_false_with_no_stated_ground_is_refused(tmp_path):
    """THE MUTATION PROOF for the #10525 review's E4 finding.

    `measurement_visible_to_author: false` was held to `prior_measurements` only, so a `false`
    for a measurement that EXISTS but is not cited was invisible to every arm: E4 shipped exactly
    that way — the field right, the reason nowhere, and the §0 row beside it asserting "no
    measurement existed", which the receipt's own `date` (2026-10-19T01:39:26Z, two days before
    the registration) refutes. A bare `false` is therefore refused, in both directions of the
    reader's question (when did it run, and was the artefact in the tree).
    """
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    _crit(rec, "E4").pop("measurement_visible_to_author_basis")
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("E4" in f and "measurement_visible_to_author_basis" in f for f in failures), failures
    # the ground is asked of the `false` direction ONLY: a `true` is already held to a citation,
    # so removing E5's basis must not red anything (it never had one).
    assert not any("E5" in f for f in failures), failures


def test_an_empty_basis_is_not_a_basis(tmp_path):
    """Whitespace is the shape a field takes when a lane fills it in to pass the rule."""
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    _crit(rec, "E3")["measurement_visible_to_author_basis"] = "   "
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("E3" in f and "measurement_visible_to_author_basis" in f for f in failures), failures


def test_e5_flipped_back_to_false_is_refused(tmp_path):
    """THE MUTATION PROOF for #9738: flip the field back and the gate goes RED.

    This is the node the issue names — the shipped pair used to read exactly this — so the test
    recreates it rather than reasoning about it.
    """
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    _crit(rec, "E5")["measurement_visible_to_author"] = False
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("E5" in f and "falsified" in f for f in failures), failures
    assert any("ecc7bc8ca" in f for f in failures), failures


def test_a_visible_measurement_with_no_citation_is_refused(tmp_path):
    """The other direction: a `true` no artefact backs is the same sentence minus the evidence."""
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    rec["prior_measurements"].pop("E5")
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("E5" in f and "uncheckable" in f for f in failures), failures


def test_a_pins_record_commit_disagreement_is_refused(tmp_path):
    """Two copies of a commit pin that can disagree sends a reader to the wrong history."""
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    rec["prior_measurements"]["E5"]["commit"] = "deadbeef0"
    rec["prior_measurements"]["E5"]["artifact"] = "nowhere.md"
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("pins.prior_measurements" in f for f in failures), failures


def test_the_e3_class_set_is_derived_from_the_writer_constant():
    """E3's `Event`/`Document` are not prose choices: they are the writer's own constant (#9736)."""
    labels = check.derivation_label_classes()
    assert {"Event", "Document"} <= set(labels), labels
    assert "Object" not in labels, labels


def test_the_real_e3_row_names_exactly_the_anchored_classes():
    """EVERY cell, not their union (#10525 review P1) — the union is what let a narrower numerator by."""
    rows = {r["id"]: r for r in check.parse_criteria_rows(PREREG.read_text())}
    anchored = set(check.SELF_ANCHORED_CLASSES) | check.derivation_label_classes()
    for cell in check.E3_CLASS_CELLS:
        named = check._classes_named(rows["E3"][cell])
        assert named == anchored, (cell, sorted(named), sorted(anchored))


def test_an_e3_row_spelling_the_document_class_as_source_is_accepted():
    """#10525 review P1, MEASURED: the honest alternative was a CI FAILURE, not a choice.

    The writer documents `_DERIVATION_REFERENCES_LABELS` as a set of RELATION SPELLINGS, not node
    labels (`edges.py:716-744`: "the discriminator is the caller-supplied RELATION spelling … not
    the target's node label"), and `edges.py:738-744` states that POST-D10 the `:Document` LABEL is
    retired — the derivation is stored as `(Source)-[:references]->(Source)`. Holding E3's cells to
    that flat set therefore REFUSED `Source` ("counts ['Source'] … an unanchored class in the
    numerator") and DEMANDED `Document`, `0` nodes in the shipped census — settling the owner's
    still-open #9736 ruling (*"the honest set may be `Point` + `Event` + `Source`-as-document … a
    question for your ruling, not something a lane may settle by picking the one that parses"*) by
    making one reading a red check.

    Non-vacuity: on the pre-fix rule this row produced TWO failures — `counts ['Source'] among its
    derived nodes` and `omits ['Document'] from its metric` — so `failures == []` reds on it.
    """
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `Event` + `:Source`) carrying a "
                  "version anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `Event` + `:Source` "
                       "(the classes the model anchors: `Point` on the node, `Event` and the "
                       "document derivation on their link; floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text())
    )
    assert failures == [], failures
    # The accepted set is DERIVED, never typed: the pair is the writer's spelling plus the node
    # label the writer's own comment names for the retired spelling.
    groups = check.e3_required_class_groups()
    assert frozenset({"Document", "Source"}) in groups, groups
    assert frozenset({"Point"}) in groups and frozenset({"Event"}) in groups, groups
    assert not any("Object" in group for group in groups), groups


def test_an_e3_row_naming_neither_document_spelling_is_still_refused():
    """The refusal that MATTERS survives: dropping the document class is #9736 one level down.

    Accepting `Source` must not turn the omission arm into a pass. A cell naming `Point` + `Event`
    and no document class at all still cannot reach a denominator that counts the document
    derivation, and the message names BOTH spellings so a reader is not sent to the one the gate
    happens to prefer.
    """
    row = {"line": "269", "metric": "", "denominator": ""}
    cells = {"metric": check._classes_named("`Point` + `Event`"),
             "denominator": check._classes_named("`Point` + `Event`")}
    failures = check._e3_class_set_failures(row, cells, check.e3_required_class_groups())
    assert len(failures) == 2, failures
    for failure in failures:
        assert "omits the document-derivation class" in failure, failure
        assert "Document" in failure and "Source" in failure, failure


def test_an_e3_row_spelled_in_prose_case_is_REFUSED_not_skipped():
    """#10525 review ROUND 3, P2 — MEASURED: case sensitivity alone bought a SKIP.

    Making the class matcher case-sensitive closed the prose-noun false accept and opened the inverse
    hole: a row whose cells are spelled `point + object + event` names no canonical class, so the
    caller's "names no graph class at all" arm skipped it — and the PRE-FIX matcher REFUSED that same
    row for naming `Object`. A lower-cased row is a SPELLING error, never a criterion measured over
    something else, so it must FAIL.

    Non-vacuity: on the round-2 code (case-sensitive `_classes_named`, no
    `_class_words_mentioned`) this row produced `failures == []` plus the "names no graph class"
    scope note, so the `len(failures) >= 1` assertion below reds on it.
    """
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (point + object + event) carrying a version "
                  "anchor",
        "denominator": "**all** derived nodes written by the run — point + object + event "
                       "(floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "270",
    })
    scopes: list[str] = []
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text()), scopes
    )
    assert len(failures) >= 1, (failures, scopes)
    assert any("LABEL spelling" in f for f in failures), failures
    assert all("names no graph class at all" not in s for s in scopes), scopes
    # and the genuine non-class row is still a DECLARED skip, not a failure
    assert check._class_words_mentioned(
        "session-arms whose receipt proves the arm's S3 state") == set()


def test_an_e3_row_naming_the_NOUN_source_instead_of_the_class_is_refused():
    """#10525 review ROUND 2, P2 — MEASURED: the ordinary English noun satisfied the arm.

    The class matcher casefolded the cell and matched the lower-case token, so E3's own wording —
    *"``Point`` + ``Event`` … that are the SOURCE of at least one ``extractedFrom`` relationship"*
    — named `{Point, Event, Source}` and PASSED, i.e. a cell naming NO document class at all was
    accepted by the very arm written to refuse it, against §7 item 6's *"a denominator that omits a
    class the model does anchor fails too"* (quoted as that rule states it — the document-class
    requirement is this arm's own derivation from it, not a sentence the registration contains).
    A Cypher node label is case-sensitive: the class is
    spelled `Source`/`Document`, the prose noun is `source`/`SOURCE` and names nothing.

    Non-vacuity: on the pre-fix matcher both spellings yielded `Source` in the named set and
    `_e3_class_set_failures` returned `[]` — so the `len(failures) == 2` below reds on it.
    """
    row = {"line": "269", "metric": "", "denominator": ""}
    for prose in ("the SOURCE of at least one `extractedFrom` relationship",
                  "the source of at least one `extractedFrom` relationship"):
        cell = f"`Point` + `Event` that are {prose}"
        named = check._classes_named(cell)
        assert named == {"Point", "Event"}, (prose, sorted(named))
        cells = {"metric": named, "denominator": set(named)}
        failures = check._e3_class_set_failures(row, cells, check.e3_required_class_groups())
        assert len(failures) == 2, (prose, failures)
        for failure in failures:
            assert "omits the document-derivation class" in failure, failure
    # ...and the CLASS spelling is still accepted, including the owner-gated retired one.
    assert "Source" in check._classes_named("`Point` + `Event` + `:Source`")
    assert "Document" in check._classes_named("`Point` + `Event` + `Document`")


def test_an_e3_numerator_narrower_than_its_denominator_is_refused():
    """THE MUTATION PROOF for #10525 P1, measured on the pre-fix code.

    With `metric` naming `Point` + `Event` and the shipped `denominator` still naming `Point` +
    `Event` + `Document`, the union test returned `[]` — a numerator that cannot reach its own
    denominator, which is #9736 one level down, passed the rule written to refuse it.
    """
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `Event`) carrying a version anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `Event` + `Document` "
                       "(the classes the model anchors; floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text())
    )
    assert any(
        "E3" in f and "omits" in f and "from its metric" in f and "Document" in f for f in failures
    ), failures
    # The denominator IS complete: the rule must name the cell that is short, not both.
    assert not any("from its denominator" in f for f in failures), failures


def test_an_e3_denominator_missing_an_anchored_class_is_refused():
    """Symmetric arm: a complete numerator does not excuse a denominator that scores a subset."""
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `Event` + `Document`) carrying a "
                  "version anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `Event` (floor ≥ 30)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text())
    )
    assert any("omits" in f and "from its denominator" in f and "Document" in f for f in failures), \
        failures
    assert not any("from its metric" in f for f in failures), failures


def test_the_two_arms_are_asked_of_the_capstone_bundle():
    """Non-vacuity for the scope: the bundle they were WRITTEN for is asked both, and skips none."""
    scopes: list[str] = []
    assert _contradictions(PREREG_DIR, scopes) == []
    assert scopes == [], scopes


def test_the_sibling_bundle_is_not_failed_by_the_arms_it_never_declared():
    """REGRESSION, measured: 7 failures on the merged tree before the scope existed (#10525).

    `docs/evidence/5087-hypothesis` lands in the SAME CI invocation as the capstone bundle
    (#10468). Its record predates `measurement_visible_to_author` entirely — no field on any
    criterion, no `prior_measurements` block — so it makes no visibility claim to contradict; and
    its E3 is a SESSION-ARM receipt check naming no graph class at all, so the anchor model, which
    never scored it, is not its subject. Six non-boolean failures plus one "omits every class" is
    what an unconditional rule says about a bundle that said none of it.
    """
    text = (SIBLING_DIR / check.PREREG_NAME).read_text()
    scopes: list[str] = []
    failures = check.check_registry_contradictions(
        SIBLING_DIR, check.parse_criteria_rows(text), check.parse_pins(text), scopes
    )
    assert failures == [], failures
    assert len(scopes) == 2, scopes
    assert any("#9738" in s for s in scopes), scopes
    assert any("names no graph class" in s for s in scopes), scopes


def test_a_caller_that_carries_no_scope_list_still_declares_the_skips(capsys):
    """A skip nobody reads is a green nobody measured — so omitting `scopes` may not silence it.

    `scopes` is optional (a caller with nowhere to put the notes need not build a list), and until
    the #10525 review's residual was closed that default cost the DECLARATION: `_note` appended
    nothing and printed nothing, so an arm this gate did not enforce left no trace anywhere. The
    no-list path now writes the same sentence to `stderr`, where this tool's `ERROR:` lines already
    go, so the two states are "carried" and "printed" and there is no third.

    Non-vacuity, measured against the pre-fix `_note`: it returned without printing when
    `scopes is None`, so `readouterr().err` was empty and both assertions below red.
    """
    text = (SIBLING_DIR / check.PREREG_NAME).read_text()
    failures = check.check_registry_contradictions(
        SIBLING_DIR, check.parse_criteria_rows(text), check.parse_pins(text)
    )
    assert failures == [], failures
    err = capsys.readouterr().err
    assert "declared, not carried" in err, err
    assert "#9738" in err, err
    assert "names no graph class" in err, err


def test_a_record_that_carries_no_visibility_field_is_not_asked_the_9738_rule(tmp_path):
    """A record that predates the field makes no claim, so there is nothing to contradict.

    The synthetic record carries E5 **and** E3 because the SUBJECT here is the missing visibility
    FIELD, not a missing criterion: the document paired with it is the real one, which registers
    both, so a record without E3 is a second, unrelated disagreement (the #10525 round-11 arm —
    see `test_a_machine_record_that_drops_its_e3_row_is_refused`). Keeping one subject per test is
    what makes a red here name a cause. `E3` is given no class names, so the class-set arm stays a
    declared skip as it does for the sibling bundle.
    """
    directory = tmp_path / "bundle"
    directory.mkdir()
    (directory / check.MACHINE_RECORD).write_text(json.dumps({
        "schema": "capstone-prereg-v1",
        "criteria": [
            {"id": "E5", "metric": "m", "denominator": "d"},
            {"id": "E3", "metric": "m", "denominator": "d"},
        ],
    }))
    scopes: list[str] = []
    failures = check.check_registry_contradictions(
        directory, check.parse_criteria_rows(PREREG.read_text()), {}, scopes
    )
    assert failures == [], failures
    assert any("#9738" in s for s in scopes), scopes


def test_dropping_one_visibility_field_does_not_opt_out_of_the_rule(tmp_path):
    """The scope is a schema declaration, NOT a per-criterion escape hatch.

    A record that declares the field anywhere still owes it EVERYWHERE: otherwise deleting the one
    criterion you would have had to disclose would be the way past a rule about disclosure.
    """
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    _crit(rec, "E3").pop("measurement_visible_to_author")
    directory = _record_copy(tmp_path, criteria=rec["criteria"])
    scopes: list[str] = []
    failures = _contradictions(directory, scopes)
    assert any("E3" in f and "not a boolean" in f for f in failures), failures
    assert not any("#9738" in s for s in scopes), scopes  # the arm APPLIED, it was not skipped


def test_a_skipped_arm_is_declared_in_the_report_and_on_the_human_path(tmp_path, capsys):
    """A skip that is not declared is the silent-green shape this tool exists to refuse."""
    code = check.main(["--dir", str(SIBLING_DIR), "--no-git", "--no-corpus", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload["failures"]
    assert payload["ok"] is True
    assert len(payload.get("registry_scopes") or []) == 2, payload.get("registry_scopes")

    code = check.main(["--dir", str(SIBLING_DIR), "--no-git", "--no-corpus"])
    out = capsys.readouterr().out
    assert code == 0
    assert "registry arm NOT applied" in out

    # …and the capstone bundle, which both arms ARE asked of, says nothing.
    code = check.main(["--dir", str(PREREG_DIR), "--no-git", "--no-corpus"])
    out = capsys.readouterr().out
    assert code == 0
    assert "registry arm NOT applied" not in out


def test_e3_counting_an_unanchored_class_is_refused(tmp_path):
    """THE MUTATION PROOF for #9736: put `:Object` back and the gate goes RED.

    `:Object` mention links carry no `sourceVersion` by design (#5199 option A), so a denominator
    counting them makes `= 1.000` unreachable — the exact row the shipped registration carried.
    """
    text = PREREG.read_text()
    mutated = text.replace(
        "| **E3** | derived nodes written by the run (`Point` + `Event` + `Document`) carrying a version anchor |",
        "| **E3** | derived nodes written by the run (`Point` + `Object` + `Event`) carrying a version anchor |",
    )
    assert mutated != text, "the shipped E3 metric row moved — this mutation test is stale"
    target = tmp_path / check.PREREG_NAME
    target.write_text(mutated)
    # the record must be READABLE for the rule to run at all: an absent record is
    # `check_machine_record`'s failure, and this rule stays silent rather than doubling it.
    (tmp_path / check.MACHINE_RECORD).write_text((PREREG_DIR / check.MACHINE_RECORD).read_text())
    pins = check.parse_pins(mutated)
    failures = check.check_registry_contradictions(
        tmp_path, check.parse_criteria_rows(mutated), pins
    )
    assert any("E3" in f and "Object" in f and "#9736" in f for f in failures), failures


def test_a_machine_record_that_drops_its_e3_row_is_refused(tmp_path):
    """THE MUTATION PROOF for the E3-existence arm — which was DEAD CODE until round 11.

    #10525 review round 11 P2: `document_registers_e3 = any(r["id"] == "E3" for r in rows)` was
    evaluated INSIDE `if not e3:`, where `e3` is that same expression — so the guard was constantly
    False and the failure it guards was unreachable. Both directions were dead: a record that
    silently DROPPED its `E3` criterion while the document kept one passed the gate with a
    `no E3 criterion is registered by this bundle` skip beside a document that registers one, i.e.
    the artefact a machine consumer reads disagrees with the document and the arm written for exactly
    that asymmetry reported nothing.

    Non-vacuity (measured at the pre-fix head): `_contradictions(<record without E3>)' == []`.
    The second half guards the DIRECTION, so a future "fix" cannot make the arm unconditional again:
    a bundle that registers E3 in neither artefact is a DECLARED skip, not a failure (the same rule
    the sibling 5087 bundle is held to).
    """
    rec = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    rec["criteria"] = [c for c in rec["criteria"] if c.get("id") != "E3"]
    assert not any(c.get("id") == "E3" for c in rec["criteria"]), "the record's E3 row moved"
    directory = tmp_path / "capstone"
    directory.mkdir()
    (directory / check.PREREG_NAME).write_text(PREREG.read_text())
    (directory / check.MACHINE_RECORD).write_text(json.dumps(rec, indent=2))
    failures = _contradictions(directory)
    assert any("no E3 row" in f for f in failures), failures

    # …and the same record against a DOCUMENT that registers no E3 either is a declared skip.
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    scopes: list[str] = []
    failures = check.check_registry_contradictions(
        directory, rows, check.parse_pins(PREREG.read_text()), scopes
    )
    assert not any("no E3 row" in f for f in failures), failures
    assert any("no E3 criterion is registered" in s for s in scopes), scopes


def test_e3_omitting_an_anchored_class_is_refused():
    """Symmetric arm: a numerator that cannot count an anchored class cannot reach its denominator.

    Built as a ROW rather than by editing the document: the shipped denominator also names
    `Document` in its explanatory clause, so a text mutation that drops the list entry still names
    the class — and this arm is about the rule, not about the document's phrasing.
    """
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `Event`) carrying a version anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `Event` (floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text())
    )
    assert any("E3" in f and "omits" in f for f in failures), failures


def test_an_unreadable_derivation_constant_is_an_error_not_a_pass(monkeypatch):
    """A gate that cannot see the model must never green the row it was written to refuse."""
    monkeypatch.setattr(check, "DERIVATION_LABELS_PATH", Path("tortoise/projection/absent.py"))
    with pytest.raises(check.CheckError):
        check.derivation_label_classes()


def test_a_colon_spelled_class_is_visible_to_the_rule():
    """#9736 review P2, MEASURED: `(?<![\\w:])` made the canonical `:Object` spelling invisible.

    Two failures in opposite directions, both from one lookbehind: a row written
    `Point` + `:Object` + `Event` (the spelling §3.4 itself uses) named only `{Point, Event}` and
    PASSED the rule written to refuse it, while a correct row spelled entirely with colons named
    nothing and was FALSELY red-ed for omitting all three classes.
    """
    assert check._classes_named("`Point` + `:Event` + `:Document`") == {"Point", "Event", "Document"}
    assert check._classes_named("`Point` + `:Object` + `Event`") == {"Point", "Object", "Event"}
    # a word that merely CONTAINS a class name is still not a class
    assert check._classes_named("the fooObject list, and the pointed remark") == set()


def test_a_colon_spelled_object_re_reds_the_e3_rule():
    """The mutation that survived the first pass: the refused class, written the canonical way."""
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `:Object` + `:Event`) carrying an anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `:Event` (`:Document` too); floor ≥ 30",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    failures = check.check_registry_contradictions(
        PREREG_DIR, rows, check.parse_pins(PREREG.read_text())
    )
    assert any("E3" in f and "Object" in f for f in failures), failures


def test_the_measurement_probe_derives_the_same_class_set():
    """#9736 must not be fixed on paper only — the INSTRUMENT's set is the same set.

    `probes/capstone_measure.py` hardcoded `("Point", "Object", "Event")` with the comment "not a
    choice this probe makes", and that WAS a choice: a re-run emitted `148/433` while the
    registration now names `148/383`. A single measured criterion may not be computed two ways.
    """
    import ast

    source = (PREREG_DIR / "probes" / "capstone_measure.py").read_text()
    assert "_DERIVATION_REFERENCES_LABELS" in source
    tree = ast.parse(source)
    assignments = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "DERIVED_LABELS" for t in node.targets)
    ]
    assert assignments, "the probe no longer defines DERIVED_LABELS — this test is stale"
    for node in assignments:
        hardcoded = isinstance(node.value, ast.Tuple) and all(
            isinstance(e, ast.Constant) for e in node.value.elts
        )
        assert not hardcoded, (
            "DERIVED_LABELS is a hardcoded literal again — it must follow "
            "`_DERIVATION_REFERENCES_LABELS` + Point, or the probe and the registration measure "
            "different criteria (#9736)"
        )


# ── the record's description of its own gate, and the routing row a reader arrives through ──


def test_the_records_e3_rule_states_the_rule_the_gate_implements():
    """§7 item 6 is the record's statement of its own MECHANICAL enforcement, so it must be true.

    #10525 review P3, MEASURED: §7 said E3's cells must name **exactly** `{Point} ∪
    _DERIVATION_REFERENCES_LABELS`, while the gate holds each cell to one ALIAS GROUP per anchored
    class — so a row spelling the document-derivation class `Source` (the node label it is stored on
    once `Document`'s label retires) PASSED a rule the record told a reader refuses it. §7 is the
    section whose whole job is to be the checkable statement of what is enforced; it stated the
    opposite, and no test compared the sentence with the code.

    The requirement is derived FROM the code (`e3_required_class_groups`), so the prose cannot drift
    again: every member of every anchored-class group must be named in §7 item 6, and the flat-set
    claim that replaced a group with a single spelling must not come back.

    Non-vacuity: at the pre-fix head the paragraph carried `**exactly**` and `{Point} ∪ that set`
    and named neither `Document` nor `Source` as alternatives, so this reds on it.
    """
    text = PREREG.read_text()
    start = text.index("**E3's class set is re-derived")
    paragraph = text[start:].split("\n\n", 1)[0]
    for group in check.e3_required_class_groups():
        for member in sorted(group):
            assert f"`{member}`" in paragraph, (
                f"§7 item 6 never names `{member}`, which the gate requires — a reader would "
                f"conclude the rule refuses a row this gate accepts: {paragraph!r}"
            )
    assert "{Point} ∪ that set" not in paragraph, (
        "§7 item 6 claims the required set is the FLAT `{Point} ∪ _DERIVATION_REFERENCES_LABELS`, "
        "but the gate holds a cell to one alias GROUP per class — the sentence is false in the "
        "direction that lets an accepted spelling read as a refused one"
    )
    assert "**exactly**" not in paragraph, (
        "§7 item 6 says the cells must name *exactly* one set; the document-derivation class has "
        "two accepted spellings, so `exactly` cannot be what is enforced"
    )


def test_section_10_restates_the_rule_section_7_states():
    """§10 is the section a lane reads to learn WHAT IS ENFORCED — it may not restore the flat set.

    #10525 review round 11 P2: §10's "What enforces both, mechanically." paragraph said E3's
    metric/denominator "must name exactly the classes derived from `_DERIVATION_REFERENCES_LABELS`
    plus `Point`" — the sentence the previous round deleted from §7, and the same falsehood: the gate
    holds each cell to one ALIAS GROUP per anchored class, so a row spelling the document-derivation
    class `Source` passes a rule §10 told the reader refuses it. The previous round's assertion was
    scoped to §7's paragraph ALONE, so two sections of one document could disagree — and did: a lane
    amending E3 to the `Source` spelling reads §10, concludes the gate will red it, and keeps
    `Document` to stay green, settling #9736's owner-gated question by prose in the direction the code
    deliberately refuses to settle.

    The requirement is DERIVED from the code (`e3_required_class_groups`), exactly as §7's is, so §10
    cannot drift back either.

    Non-vacuity: at the pre-fix head this paragraph carried `exactly the classes derived` and named
    neither `Document` nor `Source` as alternatives, so every assertion below reds on it.
    """
    text = PREREG.read_text()
    start = text.index("**What enforces both, mechanically.**")
    paragraph = text[start:].split("\n\n", 1)[0]
    assert "exactly the classes derived" not in paragraph, (
        "§10 restates the FLAT `{Point} ∪ _DERIVATION_REFERENCES_LABELS` requirement that §7 had to "
        "drop: the gate accepts either spelling of the document-derivation class"
    )
    assert "**exactly**" not in paragraph, (
        "§10 says the cells must name *exactly* one set; the document-derivation class has two "
        "accepted spellings, so `exactly` cannot be what is enforced"
    )
    for group in check.e3_required_class_groups():
        for member in sorted(group):
            assert f"`{member}`" in paragraph, (
                f"§10 never names `{member}`, which the gate requires — a reader arriving here "
                f"would draw the opposite conclusion from §7: {paragraph!r}"
            )
    assert "#9736" in paragraph and "owner-gated" in paragraph, (
        "§10 must state that WHICH spelling is honest is #9736's owner-gated question, or it reads "
        "as the gate having settled it"
    )


#: Number words §10.2's "replaced by N rows" sentence may use. The WORD is checked against the rows §0
#: actually carries, so the sentence cannot quietly keep describing a table that has since been split.
_COUNT_WORDS = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five",
    6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
}

#: A double-quoted span presented as a CITATION of a table cell — it opens after whitespace and closes
#: before whitespace or punctuation. `*"…"*` (the author's own term, emphasis) is excluded on both
#: sides, because a term is not a claim about what a row says. Kept as a STRING so this module's import
#: block does not grow: a sibling pin cites `tests/test_ci_selection.py`'s citation of a line in THIS
#: file by NUMBER (`tests/test_ci_runner_x64_install.py::test_the_relays_a_sibling_test_file_makes_land…`),
#: so one added import shifts that line and reds a file this suite does not own.
_ROW_QUOTE_PATTERN = r'(?<=[\s(])"([^"]+)"(?=[\s.,;:)—])'


def _section_0_rows() -> dict[str, str]:
    """§0's REPLACEMENT rows as `{criterion id: its first cell}`.

    §8's rule is that a protocol deviation strikes the original row rather than editing it, so the
    live rows are the ones that FOLLOW the struck original — the rows before it (`E1`/`E1b`,
    `E2a`/`E2b`) were never amended and are not what §10 describes. Hence: collect unstruck rows after
    the first struck one.
    """
    import re

    rows: dict[str, str] = {}
    in_table = False
    struck = False
    for line in PREREG.read_text().splitlines():
        if line.startswith("| criterion |"):
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            break
        if "~~" in line:
            struck = True
            continue
        cell = line.split("|")[1].strip()
        if not cell or set(cell) <= {"-"} or not struck:
            continue
        for cid in re.findall(r"\*\*(E\w+)\*\*", cell):
            rows[cid] = cell
    return rows


def test_section_10_2_describes_the_section_0_rows_the_document_carries():
    """§10.2's summary of §0 must describe the §0 that is on the page (#10525 review round 13 P2).

    MEASURED at `66bcf2bff`: §10.2 read *"replaced by two rows — `E3, E4` (still unmeasured) and
    `E5`"*, while §0 carried **three** replacement rows and E4's row said the opposite of "still
    unmeasured" — *"⛔ not 'no measurement existed': the operator audit **ran** `2026-10-19T01:39:26Z`"*.
    Round 1 split E4 out of the shared `E3, E4` row precisely because that claim was false for E4;
    the summary was never re-read against the table it points at, so it republished the pre-split
    shape AND the pre-split claim — in the section written to remove exactly that class of claim.

    Every arm is DERIVED from the document, not restated: the count word from the rows §0 carries,
    the ids from those rows' first cells, and the "still unmeasured" ground from the record's own
    `post_registration_measurement` block. Add a fourth replacement row and this reds.

    Non-vacuity: at the pre-fix head the sentence said `two` where §0 carries three rows, never named
    `` `E4` `` (it sat inside `` `E3, E4` ``), and carried `still unmeasured` for two criteria the
    shipped record measures — so all three assertions red on it.
    """
    import re

    rows = _section_0_rows()
    assert rows, "§0's replacement rows could not be parsed — this test is stale, not green"
    assert len(rows) in _COUNT_WORDS, (
        f"§0 now carries {len(rows)} replacement rows ({', '.join(sorted(rows))}) and this test can "
        "only check a sentence that spells the count up to ten — extend `_COUNT_WORDS`"
    )
    text = PREREG.read_text()
    start = text.index("§0's row is **struck, not deleted**")
    paragraph = text[start:].split("\n\n", 1)[0]

    counted = re.search(r"replaced by (\w+) rows", paragraph)
    assert counted, (
        f"§10.2 no longer states how many rows replace §0's struck row, so nothing can be compared "
        f"with the table: {paragraph!r}"
    )
    assert counted.group(1) == _COUNT_WORDS[len(rows)], (
        f"§10.2 says §0's row was replaced by {counted.group(1)!r} row(s), but §0 carries "
        f"{len(rows)} ({', '.join(sorted(rows))}) — a reader following the pointer is told a "
        f"different table from the one on the page"
    )
    for cid in sorted(rows):
        assert f"`{cid}`" in paragraph, (
            f"§0 carries a replacement row for {cid} that §10.2 never names — the summary still "
            f"describes the shape the table had before the row was split: {paragraph!r}"
        )

    record = json.loads((PREREG_DIR / "preregistration.json").read_text())
    measured = {
        crit["id"] for crit in record["criteria"] if crit.get("post_registration_measurement")
    }
    assert measured & set(rows), (
        "no §0 replacement row has a post_registration_measurement in the record — this test is "
        "stale, not green"
    )
    assert "still unmeasured" not in paragraph, (
        "§10.2 calls §0's replacement rows 'still unmeasured', but the record this bundle ships "
        f"measures {', '.join(sorted(measured & set(rows)))} after registration — the summary "
        "republishes the claim §10.3/§10.4 exist to correct"
    )


def test_section_10_quotes_section_0_as_section_0_now_reads():
    """A section may not quote a §0 row that §0 no longer carries (#10525 review round 13 P3).

    MEASURED at `66bcf2bff`: §10.3 said *"no reader concludes from §0's 'no measurement existed at
    registration' row"* — and §0 contains no such row. E3's reads "no at registration"; E4's reads
    "no measurement was *visible* at registration" with an explicit `⛔ not "no measurement
    existed"` correction, added by §10.4. §10.3's SECOND paragraph and §10.4 were updated with that
    rewrite; its first was not, so the document stated the conflated claim a page before the section
    that corrects it — the #9738 defect inside the amendment written to remove it.

    The quote set is DERIVED: every non-italicised quote in the paragraph is looked up in §0's table
    (the struck original included, so quoting the preserved row is allowed). Pre-fix, the one quote
    was a row the table does not carry, so this reds.
    """
    import re

    text = PREREG.read_text()
    table_start = text.index("| criterion |")
    table_end = text.index("⇒ §0's table was wrong about E5", table_start)
    table = text[table_start:table_end]

    start = text.index("That is disclosed here so no reader concludes from §0's")
    paragraph = text[start:].split("\n\n", 1)[0]
    quotes = re.findall(_ROW_QUOTE_PATTERN, paragraph)
    assert quotes, (
        f"§10.3 quotes no §0 row at all, so this test is stale rather than green: {paragraph!r}"
    )
    missing = [q for q in quotes if q not in table]
    assert not missing, (
        f"§10.3 quotes {missing!r} as §0's, and §0's table carries no such text — the quote is "
        "either a row that was rewritten (§10.4) or one that never existed"
    )


def test_a_source_spelled_e3_row_declares_the_probe_cannot_see_it():
    """The gate accepts `Source` and must SAY that it does not check the instrument (#10525 P2).

    Either spelling of the document-derivation class passes (`RETIRED_SPELLING_LABELS`, #9736 is
    owner-gated), but nothing in the tool reads `probes/capstone_measure.py`: the probe's
    `DERIVED_LABELS` is derived from the writer's constant, so no sentence in the record can move
    it and a `Source` registration registers a node class the probe never enumerates. Enforcing it
    would settle #9736 (a red check on the owner's open alternative); saying nothing would let a
    registration and its instrument part company with no trace. So it is DECLARED — and this test
    is the trace.

    Non-vacuity: on the pre-fix gate `scopes` stayed `[]` for the `Source` row, because no arm
    compared the spelling with anything — so the `assert scopes` below reds on it.
    """
    text = PREREG.read_text()
    pins = check.parse_pins(text)
    rows = [r for r in check.parse_criteria_rows(text) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "derived nodes written by the run (`Point` + `Event` + `:Source`) carrying a "
                  "version anchor",
        "denominator": "**all** derived nodes written by the run — `Point` + `Event` + `:Source` "
                       "(floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    scopes: list[str] = []
    failures = check.check_registry_contradictions(PREREG_DIR, rows, pins, scopes)
    assert failures == [], failures
    assert len(scopes) == 1, scopes
    note = scopes[0]
    assert "probes/capstone_measure.py" in note, note
    assert "Source" in note, note
    assert "DERIVED_LABELS" in note, note

    # …and the SHIPPED row (the writer's `Document` spelling) declares nothing, because the
    # registration and the probe agree by construction: a note here would be the false red.
    shipped: list[str] = []
    assert check.check_registry_contradictions(
        PREREG_DIR, check.parse_criteria_rows(text), pins, shipped) == []
    assert shipped == [], shipped


def test_the_records_e3_rule_claims_no_enforcement_over_the_probe():
    """§7 item 6 must not promise a check on the INSTRUMENT that the gate cannot perform.

    #10525 review P2: the paragraph said a `Source` registration "also moves the probe's
    `DERIVED_LABELS` with it (§10.1) — otherwise the registration and the measurement part
    company, which is the very divergence this rule exists to refuse." Two clauses that cannot both
    hold: the gate accepts either spelling and never reads the probe, and the probe's set is derived
    from the writer's constant, so the RECORD cannot move it. §7 is the section whose whole job is
    to be the checkable statement of what is enforced, so a promise of enforcement it does not have
    is the defect class this bundle exists to remove — and it sat in the sentence a lane reads when
    it is deciding how to spell the class.

    What must be there instead: the obligation stated as an obligation ON THE AMENDMENT, and the
    disclosure that the gate DECLARES the divergence rather than refusing it.

    Non-vacuity: at the pre-fix head the paragraph carried "the very divergence this rule exists to
    refuse" and never named the probe's path or the declared note, so this reds on it.
    """
    text = PREREG.read_text()
    start = text.index("**E3's class set is re-derived")
    paragraph = text[start:].split("\n\n", 1)[0]
    assert "the very divergence this rule exists to refuse" not in paragraph, (
        "§7 item 6 claims the rule REFUSES a registration/instrument divergence; the gate accepts "
        "either spelling and never reads `probes/capstone_measure.py`, so the claim is false"
    )
    assert "probes/capstone_measure.py" in paragraph, (
        "§7 item 6 must name the instrument it cannot check, or a lane reads the sentence as a "
        "check that exists"
    )
    assert "obligation" in paragraph and "amendment" in paragraph, (
        "moving the probe with the spelling is the AMENDMENT's obligation — §7 must say so rather "
        "than attribute it to the rule"
    )
    assert "declare" in paragraph, (
        "the gate declares a `Source` row's divergence (`_note_unchecked_probe_spelling`); §7 must "
        "state that instead of claiming a refusal"
    )
    # The gate really is what §7 now describes: the declared-note helper exists and is reachable
    # from the class-set arm, so the prose is checked against the code, not against itself.
    assert callable(check._note_unchecked_probe_spelling)
    source = (REPO_ROOT / "tools" / "capstone_preregistration_check.py").read_text()
    assert "_note_unchecked_probe_spelling(scopes, directory, row, cells)" in source, (
        "the helper must be CALLED by the class-set arm, or §7 describes a declaration that is "
        "never emitted"
    )


def test_the_routing_index_carries_both_e3_readings():
    """A reader arrives through `docs/00_index.md`, so the amendment has to reach its rows too.

    #10525 review P2: §10.1, the README and the E-row audit were all extended with the amended
    reading, and the index row was not — it still published `148/433` and "the frozen `= 1.000` is
    unreachable as registered", which is the interpretation §10 records as the registration's
    DEFECT. The surface the repo routes readers through therefore said the amendment had not
    happened, which is the artefact-contradicts-artefact class §10 exists to remove.

    #10525 review P2 (second round): the file carries TWO rows about this bundle, and the fix for
    the first left the second stating the falsified history the amendment strikes —
    `docs/00_index.md`'s PRE-REGISTRATION row still read "E3/E4/E5 have no measurement anywhere
    and are genuinely pre-run", with no test referencing it, so nothing redded and it could not
    re-stale visibly. §10.2 (E5's measurement precedes the record by two days), §10.3 (E3's
    census postdates it) and §10.4 (E4's audit RAN, only its artefact landed late) are the record
    of why that sentence is false for two of the three criteria. Both rows are read here, for the
    reason the config note already records for #10382: a pin that reads one line of a file that
    carries two cannot claim the file is clean.

    Non-vacuity: the pre-fix `#9080` row carried neither `148/383` nor the `OPEN` marker, and the
    pre-fix PRE-REGISTRATION row still carried "no measurement anywhere" with none of
    `visible`/`ecc7bc8ca`/`d50217273`, so each leg reds on the head it was written against.
    """
    index = (REPO_ROOT / "docs" / "00_index.md").read_text().splitlines()

    e3_rows = [ln for ln in index if ln.startswith("| **`#7949` / `#9080`")]
    assert e3_rows, "docs/00_index.md no longer routes readers to the #7949/#9080 capstone row"
    row = e3_rows[0]
    assert "148/433" in row, "the AS-REGISTERED reading must stay published — it is not erased"
    assert "148/383" in row and "0.38642" in row, (
        "the index row publishes only the pre-amendment reading; §10.1 re-registers the denominator "
        f"to the anchored class set, so the row is stale: {row[:200]!r}"
    )
    assert "#9736" in row and "OPEN" in row, (
        f"the row must say #9736 remains OPEN and owner-gated — the amendment does not close it: {row[:200]!r}"
    )

    # ⛔ The SECOND row of the same file, and the same defect class: the §0 history claim.
    prereg_rows = [
        ln for ln in index if ln.startswith("| **`#7949` capstone PRE-REGISTRATION")
    ]
    assert prereg_rows, (
        "docs/00_index.md no longer routes readers to the PRE-REGISTRATION row — this leg would "
        "pass on an index that stopped routing the bundle at all")
    prereg = prereg_rows[0]
    # The two sentences this row has carried — §0's original (verbatim in §10.2's "Old value") and
    # the index's own restatement of it. Refused by their exact claim, not by the fragment: the
    # corrected row may QUOTE the stale claim to say it was wrong.
    for stale_claim in ("E3/E4/E5 have no measurement anywhere",
                        "no measurement exists anywhere"):
        assert stale_claim not in prereg, (
            "the routing index still states the §0 claim this registration STRIKES — E5's "
            f"measurement predates the record by two days (#9738, §10.2): {prereg[:400]}")
    assert "visible" in prereg, (
        "the row must say no measurement was VISIBLE to the author, not that none existed — the "
        f"distinction is what §10.4 rests on: {prereg[:400]}")
    assert "ecc7bc8ca" in prereg, (
        "E5's prior measurement must be named on the row readers arrive through (§10.2). "
        f"{prereg[:400]}")
    assert "d50217273" in prereg, (
        "E4's audit RAN before the record and only its artefact landed late (§10.4) — the row must "
        f"carry that disclosure: {prereg[:400]}")


def test_a_skipped_arm_names_the_bundle_that_skipped_it(tmp_path):
    """A declared skip must name its OWN subject (#10525 review P3, MEASURED).

    The class-set note hardcoded `docs/evidence/5087-hypothesis`'s evidence, and it is emitted for
    WHICHEVER bundle skipped the arm — so stripping the class names from the CAPSTONE's own E3 row
    made the gate explain the capstone's skip with the sibling's session-arm receipt criterion. A
    skip whose declaration names the wrong subject reads as a checked one, which is the class this
    tool exists to refuse.

    Non-vacuity: at the pre-fix head the note carried `docs/evidence/5087-hypothesis's E3 is a
    session-arm receipt check` for this bundle, so both assertions red on it.
    """
    directory = tmp_path / "a-named-bundle"
    directory.mkdir()
    _record_copy(directory)
    rows = [r for r in check.parse_criteria_rows(PREREG.read_text()) if r["id"] != "E3"]
    rows.append({
        "id": "E3",
        "metric": "every derived node the run writes carries a version anchor",
        "denominator": "every derived node the run writes (floor ≥ 30 nodes)",
        "threshold": "`= 1.000` (zero unanchored)",
        "line": "269",
    })
    scopes: list[str] = []
    failures = check.check_registry_contradictions(
        directory, rows, check.parse_pins(PREREG.read_text()), scopes
    )
    assert failures == [], failures
    class_set = [s for s in scopes if "names no graph class" in s]
    assert class_set, scopes
    assert all("a-named-bundle" in s for s in class_set), class_set
    assert not any("5087-hypothesis" in s for s in class_set), (
        f"the skip for THIS bundle is explained with a sibling's evidence: {class_set}"
    )

# ── the multi-bundle CLI: `--dir` is repeatable, a failure names its bundle (#10468) ────────

def test_a_multi_bundle_json_error_names_the_bundle_it_came_from(tmp_path, capsys):
    """REGRESSION (#10468 review, obs 1): the multi-bundle JSON dropped the directory.

    The human path prints `ERROR: {directory}: {message}`; the JSON branch emitted bare strings,
    so a consumer of a two-bundle run could see *that* one failed and not *which*. Not cosmetic —
    the only CI invocation of this tool now passes `--dir` twice (`#10468` r6 F1), and exit 2
    ("could not check this subject") deliberately outranks the readable bundle's green.
    """
    empty = tmp_path / "not-a-bundle"
    empty.mkdir()
    code = check.main(["--dir", str(PREREG_DIR), "--dir", str(empty),
                       "--no-git", "--no-corpus", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 2, "an unreadable subject must outrank the other bundle's green"
    assert payload["ok"] is False
    assert payload["reports"], "the readable bundle's report must survive the other's error"
    assert [e["directory"] for e in payload["errors"]] == [str(empty)]
    assert f"no {check.PREREG_NAME}" in payload["errors"][0]["error"]


# ── #10463: the ordering is measured against the BASE, not against the contribution ────────────
#
# WHY THESE TESTS EXIST. The rule these replace compared two add-commits INSIDE one contribution,
# which is not squash-invariant: a squash merge rewrites those commits into one, so the same tree
# passed its PR checks and redded `main` minutes later (#10468). The property the epic wants —
# "no number is scored against a threshold whose author could already see it" — survives the
# change, because a registration on the base is a published artefact that a later squash cannot
# rewrite. What must be tested is therefore (a) a registration that WAS published first passes,
# (b) one added by the SAME change is REFUSED — that refusal is the whole point, since it is the
# arm that fires at PR time instead of after the merge — and (c) the strictness survived: equal
# epochs are still not "before".
#
# ⚠️ NOT covered, and not claimed: these arms read ADD commits only (`--diff-filter=A`), so a
# contribution that rewrites a threshold in the registration AND lands a scored log in one commit is
# accepted. Closing that needs a modification bound on the ordinary arm (the exemption arm already
# has one, `_touch_epochs`) and is a new rule — tracked as **#10555**, not invented here.


def _repo_with_published_base(tmp_path: Path, monkeypatch) -> Path:
    """A throwaway repo carrying a PUBLISHED base ref (`origin/main`) the check can measure against.

    The base is a remote-tracking ref on purpose: it does not move when the contribution commits, so
    a test can land a registration, publish it, and then add a log — which is the shape a
    two-PR contribution actually has.
    """
    repo = _repo(tmp_path, monkeypatch)
    anchor = repo / "README.md"
    anchor.write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base", epoch=1_700_000_000)
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _publish_base(repo: Path) -> None:
    """Advance the published base to the current commit — "the registration landed in an earlier PR"."""
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")


def _commit_prereg(repo: Path, epoch: int, text: str = "x\n") -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repo, "add", str(target.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "add prereg", epoch=epoch)
    return target


def test_a_registration_published_on_the_base_lets_a_later_log_pass(tmp_path, monkeypatch):
    """The shape the rule ASKS FOR: the registration lands first, in its own PR; the log follows.

    This is the case #10468 was: on the branch the two commits were ordered, and the squash merge
    collapsed them. Here the registration is published (so a squash of the LOG's merge cannot touch
    it), and the log's add-commit is measured in the contribution that carries it.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    prereg = _commit_prereg(repo, epoch=1_700_000_100)
    _publish_base(repo)                       # the registration's own PR merged
    log = _write_and_commit(repo, "s3-ablation.log", epoch=1_700_000_200)
    assert check.check_ordering(prereg, [log]) == []


def test_a_registration_added_by_the_same_change_is_refused(tmp_path, monkeypatch):
    """THE NEW ARM (#10463): both artefacts in one contribution has nothing on the base to appeal to.

    Refusing here is what makes this a gate rather than a red `main`: the merged history would carry
    the two in one commit, where the ordering cannot be shown at all. The message must name the
    remedy — land the registration alone, then add the log — not merely say "ordering violated".
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    prereg = _commit_prereg(repo, epoch=1_700_000_100)   # NOT published: same change
    log = _write_and_commit(repo, "s3-ablation.log", epoch=1_700_000_200)
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1, failures
    assert "not on the merge base" in failures[0], failures[0]
    assert "origin/main" in failures[0], failures[0]
    assert "own PR" in failures[0], failures[0]


def test_the_refusal_is_not_a_no_op_when_the_base_is_missing(tmp_path, monkeypatch):
    """A checkout WITH a remote but NO resolvable base FAILS CLOSED — exit 2, never green.

    Losing this arm would let an environment change silently downgrade the check to the
    PR-internal rule this change exists to remove — a gate that passes because it could not read its
    subject. ⛔ It is a PLAIN `CheckError` (exit 2), deliberately NOT `HistoryUnavailable`: that
    class IS the non-failing third outcome `run()` folds to `ok=True`, and raising it here made the
    tool exit 0 on a full-history checkout that could not name its base (review cycle 1, finding 1).
    `HistoryUnavailable` remains the shallow-clone answer only.
    """
    repo = _repo(tmp_path, monkeypatch)
    # Force a branch name that is NOT `main`, so the only reason the base is unresolvable is the
    # one under test, and give the repo a remote so the HEAD fallback must not apply.
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/contrib")
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    anchor = repo / "README.md"
    anchor.write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base", epoch=1_700_000_000)
    # The registration is the REAL document, not a stub (review cycle 2, finding 3): with `"x\n"`,
    # `run()` refused on the missing pins block BEFORE reaching the base arm, so the test passed even
    # with F1 fully reverted and could not tell the fail-open from the fix.
    prereg = _commit_prereg(
        repo, epoch=1_700_000_100,
        text=(PREREG_DIR / check.PREREG_NAME).read_text())
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_200)
    # ⛔ Asserted through the ENTRY POINT, not just the raise (review cycle 1, finding 6): a previous
    # version of this test asserted only `pytest.raises`, and the exception was one `run()` folded
    # into the NON-FAILING third outcome — so the tool exited 0 while the test passed. The property
    # is "the run does not report success", and only exit 2 can express it.
    with pytest.raises(check.CheckError) as exc:
        check.check_ordering(prereg, [log])
    assert "no base ref" in str(exc.value), str(exc.value)
    # …and it must NOT be the NON-FAILING third outcome: `HistoryUnavailable` subclasses CheckError,
    # so `pytest.raises(CheckError)` alone cannot distinguish the fail-open from the fix.
    assert not isinstance(exc.value, check.HistoryUnavailable), type(exc.value).__name__
    # …and the ENTRY POINT must not report success either: 2 ("cannot read my subject"), never 0.
    assert check.main(["--dir", str(repo / "docs" / "evidence" / "7949-capstone"),
                       "--no-corpus"]) == 2


def test_equal_epochs_on_the_base_are_still_not_before(tmp_path, monkeypatch):
    """The STRICTNESS survives the change: "same second is not before…" is not relaxed by it.

    Both artefacts already published, added at the same epoch — the squash-merged shape that redded
    `main` (#10468) and the reason the log was withdrawn. A registration that cannot be shown to
    precede its log is not a registration that preceded it.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    prereg = _commit_prereg(repo, epoch=1_700_000_100)
    same = 1_700_000_100
    log = _write_and_commit(repo, "capstone.log", epoch=same)
    _publish_base(repo)
    failures = check.check_ordering(prereg, [log])
    assert failures, "equal epochs must not pass as 'before'"
    assert "not strictly after" in failures[0], failures[0]

    # ⛔ …and the arm must DISCRIMINATE (review cycle 3, finding 3). The shape above cannot: its two
    # add-commits are equal in HEAD's history too, so the pre-fix PR-internal predicate refuses it as
    # well. This second shape can. The base added BOTH artefacts in ONE commit while the
    # contribution's own history has them ordered, so the pre-fix reading passes it and the base
    # reading refuses it — the measured difference between the two rules.
    (tmp_path / "squashed").mkdir()
    repo = _repo_with_published_base(tmp_path / "squashed", monkeypatch)
    base = _squashed_base(repo, epoch=1_700_000_100)
    squashed_prereg = _commit_prereg(repo, epoch=1_700_000_100)
    squashed_log = _write_and_commit(repo, "capstone.log", epoch=1_700_000_200)
    # the PRE-FIX predicate is satisfied here, which is what makes the refusal below evidence
    assert check._added_epochs(squashed_prereg)[0] < check._added_epochs(squashed_log)[0]
    strict = check.check_ordering(squashed_prereg, [squashed_log], base_ref=base)
    assert strict, "the base's equal epochs must still refuse an ordered-looking contribution"
    assert "not strictly after" in strict[0], strict[0]
    # …and it names the remedy that can actually clear it (finding 1c), not an ordering to fix.
    assert "WITHDRAWAL" in strict[0], strict[0]


def test_the_base_ref_refusal_names_the_cause_that_actually_fired(tmp_path, monkeypatch):
    """BOTH branches of `no_base_ref_message`, because the explicit one is the CI shape.

    The CI step ALWAYS passes `--base-ref "origin/<branch>"`, so the operator's most likely failure
    is "that ref is not fetched here" — not "you forgot the flag". The shared message told them the
    flag was absent (it was not) and that `origin/main`/`main` did not resolve (they do), sending
    them to debug flag wiring instead of fetching the branch. Asserted in BOTH directions: naming
    the cause is only worth anything if the other cause's text is absent (#10463 review cycle 4).
    """
    repo = _repo(tmp_path, monkeypatch)
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/contrib")
    _git(repo, "remote", "add", "upstream", "https://example.invalid/x.git")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base", epoch=1_700_000_000)
    prereg = _commit_prereg(
        repo, epoch=1_700_000_100, text=(PREREG_DIR / check.PREREG_NAME).read_text())

    explicit = check.no_base_ref_message("origin/nope")
    assert "origin/nope" in explicit, explicit
    assert "no --base-ref" not in explicit, explicit
    assert "exit 2" in explicit and "third outcome" in explicit, explicit

    implicit = check.no_base_ref_message(None)
    assert "no --base-ref" in implicit, implicit
    assert "origin/nope" not in implicit, implicit
    assert "exit 2" in implicit and "third outcome" in implicit, implicit

    # …and the caller really does pass the explicit ref through, so the branch above is reachable.
    log = _write_and_commit(repo, "e1-e2.log", epoch=1_700_000_200)
    with pytest.raises(check.CheckError) as exc:
        check.check_ordering(prereg, [log], base_ref="origin/nope")
    assert "origin/nope" in str(exc.value), str(exc.value)
    assert "no --base-ref" not in str(exc.value), str(exc.value)


def test_the_measured_base_is_printed_for_a_human_not_only_for_json(capsys, monkeypatch):
    """REGRESSION (#10463 review cycle 4/5): the reporting field must reach the line a human reads.

    `ordering_base` was added so a green line could not read the same whether it measured the
    published base or this branch's own `HEAD` fallback — which IS the PR-internal rule #10463
    removes. It lived only in `--json`, so the CI log still could not tell them apart, and deleting
    the print restored that silent-green with the whole suite green (measured by a reviewer: a
    mutant, no test moved). THIS test is what makes deleting the PRINT red; the `setdefault` is
    guarded one test below (`test_a_skipped_git_half_says_the_base_was_never_measured`) — on every
    `use_git=True` path the key is assigned in the `try` before this print is reached, so it is
    legitimately a no-op here (review cycle 6, finding 2).

    The expectation is read from the REPORT, not from a second `resolve_base_ref()` call (review
    cycle 6, finding 1): re-resolving renders the literal `None` in a checkout with no resolvable
    base — the depth-1 shape the sibling test below commits as LEGITIMATE — so a second resolve reds
    a correctly-ordered tree. Careful with the two strings, which are NOT interchangeable:
    `ordering_base_candidate` carries `(unresolved)` when the candidates are checked and rejected,
    while `ordering_base` — the base the ordering was actually MEASURED against — says
    `(not measured)`, and the candidate line is suppressed rather than printed when it is
    UNRESOLVED. Asserting `(unresolved)` against `ordering_base` reds an ordered tree.
    """
    # ⛔ One resolution, not two (review cycle 7, finding 3): `main()` honours
    # `$PREREG_ORDERING_BASE_REF` and `check.run()` does not, so an ambient value would make the
    # print and the expectation come from different code paths — and the test would red a
    # legitimate, documented configuration. The env var is cleared so both sides ask the same
    # question, and the explicit-ref path is covered by its own test.
    monkeypatch.delenv(check.BASE_REF_ENV, raising=False)
    assert check.main(["--dir", str(PREREG_DIR), "--no-corpus"]) == 0
    out = capsys.readouterr().out
    report = check.run(PREREG_DIR, use_git=True, use_corpus=False)
    assert f"ordering measured against: {report['ordering_base']}" in out, out


def test_a_skipped_git_half_says_the_base_was_never_measured(capsys):
    """`--no-git` skips the ordering, so the base is `(not measured)` — never an implied one.

    The `setdefault` is what carries this: without it the key is simply ABSENT, and a consumer (or a
    human) reads "no base" as "the default base", which is the same silent-green one field over. The
    field is asserted through `--json` here so the machine surface is pinned too.
    """
    assert check.main(["--dir", str(PREREG_DIR), "--no-corpus", "--no-git"]) == 0
    out = capsys.readouterr().out
    assert "ordering measured against: (not measured)" in out, out
    report = check.run(PREREG_DIR, use_git=False, use_corpus=False)
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    # …and the MACHINE surface, through the CLI, which is what the docstring claims (review cycle 7,
    # finding 2: the sentence said `--json` was pinned while the test never passed `--json`, so the
    # claim guarded nothing). Asserted, so the claim is now the check it describes.
    assert check.main(["--dir", str(PREREG_DIR), "--no-corpus", "--no-git", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ordering_base"] == "(not measured)", payload["ordering_base"]

# ── #10463 review cycle 3: the arms the tests must actually discriminate ───────────────────────
#
# WHAT CHANGED AND WHY THESE EXIST. Cycle 3 found (1) an acceptance assertion that cannot hold in a
# depth-1 checkout, (3) two added tests that a full revert of the new predicate still passes, and
# (1c) an equal-epoch refusal that names no remedy. Each test below fails on the pre-fix code.
# (Cycle 3's finding 4 — the base-ref refusal misstating its own cause — is pinned by
# `test_the_base_ref_refusal_names_the_cause_that_actually_fired` above; cycle 4 landed it with
# both branches asserted, so it is deliberately not duplicated here.)


def _git_out(repo: Path, *args: str) -> str:
    """stdout of a read-only git call, stripped."""
    out = subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _write_prereg_uncommitted(repo: Path, text: str = "x\n") -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    return target


def _stage_log(repo: Path, name: str, body: str = "x\n") -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return target


def _commit_together(repo: Path, epoch: int, names: tuple[str, ...] = ("capstone.log",)) -> None:
    """Add the registration AND its logs in ONE commit — the shape a squash merge produces."""
    _write_prereg_uncommitted(repo)
    for name in names:
        _stage_log(repo, name)
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "the registration and its log in one commit", epoch=epoch)


def _squashed_base(repo: Path, epoch: int, names: tuple[str, ...] = ("capstone.log",)) -> str:
    """A ref adding the registration AND its log in ONE commit, NOT an ancestor of the contribution.

    That is what a squash-merged `main` looks like to a branch that has not been rebased: equal
    epochs on the base, so the base reading refuses while the contribution's own ordering may be
    strict. ⛔ NOT the only such shape (review cycle 8, finding 1, measured): a base whose log
    predates its registration — two separate commits — discriminates the two readings just as well,
    and is witnessed separately by
    `test_a_base_where_the_log_predates_the_registration_refuses_an_ordered_contribution`. A future
    lane tempted to delete this helper because "the other test covers it" should know both exist.
    """
    branch = _git_out(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "squashed-base")
    _git(repo, "rm", "-rq", "--cached", "--ignore-unmatch", ".")
    _commit_together(repo, epoch=epoch, names=names)
    sha = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qf", branch)
    return sha


def test_a_depth_one_checkout_without_a_base_reports_the_unresolved_base(tmp_path, monkeypatch):
    """REGRESSION (#10463 review cycle 3, finding 2): the reported base must tolerate depth 1.

    The acceptance test asserts `report["ordering_base"]` is one of the resolved candidates. In a
    depth-1 checkout of a single branch there is no `origin/main` and no local `main`, so the honest
    report is `(not measured)` — with `(unresolved)` recorded separately as the candidate — and an
    assertion that insists on a resolved ref reds a correctly
    ordered tree from inside the test written to prevent exactly that. The value must stay a
    reported fact; what it must NOT do is turn the unprovable third outcome into a failure.
    """
    origin = _repo(tmp_path, monkeypatch)
    _seed_with_the_real_bundle(origin)
    _git(origin, "add", "docs", "tortoise")
    _git(origin, "commit", "-q", "-m", "the real bundle", epoch=1_700_000_100)
    _git(origin, "branch", "-m", "contrib")

    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--single-branch", "--branch", "contrib",
         f"file://{origin.resolve()}", str(shallow))
    monkeypatch.setattr(check, "REPO_ROOT", shallow.resolve())
    assert check.history_unavailable() is True, "the fixture must really be a depth-1 checkout"

    report = check.run(shallow / "docs" / "evidence" / "7949-capstone", use_git=True,
                       use_corpus=False)
    assert report["ok"], report["failures"]
    # ⛔ `(not measured)`, NOT the resolved-if-it-were base (review cycle 7, finding F2): this
    # fixture has no `origin/main` at all, so the CANDIDATE is unresolved — but the field is named
    # for what was measured, and in a shallow checkout nothing is. Both facts are asserted so this
    # test can no longer be satisfied by the fixture's absent ref alone (see the sibling test below,
    # whose depth-1 clone of `main` DOES carry `origin/main`).
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["ordering_base_candidate"] == check.UNRESOLVED_BASE, \
        report["ordering_base_candidate"]
    # …and the third outcome is what it is: UNPROVABLE, never "violated".
    assert report["ordering"] == check.HISTORY_UNAVAILABLE, report["ordering"]


def test_the_base_ref_is_what_decides_the_pass_not_the_contribution(tmp_path, monkeypatch):
    """The PASS arm, made discriminating by its SUBJECT (review cycle 3, finding 3).

    A pass arm alone cannot tell the two predicates apart: adds on the base are a subset of the adds
    on the contribution, so `min(base) >= min(HEAD)` and the pre-fix PR-internal predicate accepted
    every tree the base predicate accepts. What does distinguish them is which ref the verdict was
    measured against — so the same tree is asked twice: against the published base it passes, and
    against a base that predates the registration it REFUSES. The pre-fix code had no base at all,
    so both answers were identical, which is the defect.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    stale = _git_out(repo, "rev-parse", "HEAD")   # the base BEFORE the registration is published
    prereg = _commit_prereg(repo, epoch=1_700_000_100)
    _publish_base(repo)                           # the registration's own PR merged
    log = _write_and_commit(repo, "s3-ablation.log", epoch=1_700_000_200)

    assert check.check_ordering(prereg, [log]) == []
    pinned = check.check_ordering(prereg, [log], base_ref=stale)
    assert len(pinned) == 1, pinned
    assert "not on the merge base" in pinned[0], pinned[0]
    assert stale in pinned[0], f"the refusal must name the base it measured: {pinned[0]}"


def test_a_squash_collapsed_pair_names_the_withdrawal_remedy(tmp_path, monkeypatch):
    """The equal-epoch refusal must name the remedy that can actually clear it (cycle 3, 1c).

    When both artefacts share their add-commit on the base, no re-ordering can help — this is what
    redded `main` at `cfa3de857` (#10468), and the only path back to green is a WITHDRAWAL of the
    log (#10544). A message that only says "not strictly after" sends the operator hunting for an
    ordering to fix that does not exist.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    _commit_together(repo, epoch=1_700_000_100)
    _publish_base(repo)                     # both landed on the base in ONE squash commit
    prereg = repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME
    log = repo / "docs" / "evidence" / "7949-capstone" / "capstone.log"

    failures = check.check_ordering(prereg, [log])
    assert failures, failures
    assert "not strictly after" in failures[0], failures[0]
    assert "WITHDRAWAL" in failures[0], failures[0]
    assert "SAME commit" in failures[0], failures[0]
    # It names the offending commit, so the operator can look at what collapsed.
    assert _git_out(repo, "rev-parse", "--short=12", "HEAD") in failures[0], failures[0]


# ── #10463 review cycle 7: the remedy that could not work, and a base that was never measured ──
#
# TWO findings, both in surfaces an operator or a consumer READS rather than in the comparison:
#   F1  the equal-epoch refusal ended by naming "re-add the log in a PR of its own" — a move that
#       cannot clear the check (the collapsed add-commit stays in the base's history, so `min()` is
#       still the collapsed epoch) AND that the ruling forbids (#10463 comment 6564634454 item 2).
#   F2  `ordering_base` was assigned the RESOLVED candidate before `check_ordering` ran, so a
#       shallow checkout that raised before reading an epoch still printed "measured against:
#       origin/main" — a measurement that did not happen. The sibling depth-1 test could not catch
#       it because its fixture clones a branch with no `origin/main` at all.
# Each test below fails on the pre-fix code.


def test_a_withdrawn_log_cannot_be_reordered_by_re_adding_it(tmp_path, monkeypatch):
    """F1: the recipe the old refusal named cannot clear the check — measured, all the way through.

    `_added_epochs_on` is `git log --diff-filter=A <base> -- <path>`, and a later DELETION does not
    remove the earlier add from that ref's history. So the operator follows the old instruction to
    the end — withdraw the log in its own PR, let it land, re-add the log in a PR of its own, let
    THAT land — and the base still reports the collapsed add-commit, `min()` is still the collapsed
    epoch, and the ref emits the same refusal. There is no later state to reach, which is why the
    only honest remedy is a new artefact at a new path.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    _commit_together(repo, epoch=1_700_000_100)     # both artefacts in ONE squash commit
    _publish_base(repo)                            # …which is what `origin/main` carries
    bundle = repo / "docs" / "evidence" / "7949-capstone"
    prereg, log = bundle / check.PREREG_NAME, bundle / "capstone.log"

    # Step 1 — the withdrawal (#10544): the log leaves the checked directory, kept under `withdrawn/`.
    (bundle / "withdrawn").mkdir()
    (bundle / "withdrawn" / "capstone.log").write_text("x\n")
    log.unlink()
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "withdraw the log, keep it under withdrawn/", epoch=1_700_000_200)
    _publish_base(repo)

    # Step 2 — the old instruction's next step: "re-add the log in a PR of its own", and let it land.
    log.write_text("x\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "re-add the log to obtain a later add-commit", epoch=1_700_000_300)
    _publish_base(repo)

    # The premise, as a fact about git rather than prose: the base now holds BOTH adds, and the
    # epoch the tool takes is the COLLAPSED one.
    base_adds = check._added_epochs_on("origin/main", log)
    assert len(base_adds) == 2, base_adds
    assert min(base_adds) == 1_700_000_100, base_adds

    failures = check.check_ordering(prereg, [log])
    assert failures, "a re-added log must not turn the refusal green"
    assert "not strictly after" in failures[0], failures[0]
    assert "WITHDRAWAL" in failures[0], failures[0]
    # ⛔ …and the message must not instruct the move it just refuted, nor the one the ruling forbids.
    assert "re-add the log in a PR of its own" not in failures[0], failures[0]
    assert "Do NOT re-add" in failures[0], failures[0]
    assert "NEW artefact at a NEW path" in failures[0], failures[0]


def test_a_depth_one_clone_of_main_reports_no_measured_base_even_though_one_resolves(
        tmp_path, monkeypatch, capsys):
    """F2: `ordering_base` must not name a base on a tree where nothing was measured.

    ⛔ The shape matters, and this is the one the previous fixture could not build: a `--depth 1`
    clone of `main` DOES carry `refs/remotes/origin/main` (measured — `resolve_base_ref` returns it),
    while `history_unavailable()` is True so `check_ordering` raises before reading a single epoch.
    The pre-fix code assigned the resolved ref to `ordering_base` BEFORE that raise, so the CI log —
    the surface a human reads — printed `ordering measured against: origin/main` for a run that
    measured nothing. The resolved ref is now reported as a CANDIDATE, on its own line, and the
    measured line says what actually happened.
    """
    origin = _repo(tmp_path, monkeypatch)
    _seed_with_the_real_bundle(origin)
    _git(origin, "add", "docs", "tortoise")
    _git(origin, "commit", "-q", "-m", "the real bundle", epoch=1_700_000_100)
    _git(origin, "branch", "-m", "main")

    shallow = tmp_path / "shallow-main"
    _git(tmp_path, "clone", "-q", "--depth", "1", "--branch", "main",
         f"file://{origin.resolve()}", str(shallow))
    monkeypatch.setattr(check, "REPO_ROOT", shallow.resolve())
    assert check.history_unavailable() is True, "the fixture must really be a depth-1 checkout"
    # ⛔ The property the old fixture lacked: the candidate RESOLVES here.
    assert check.resolve_base_ref() == "origin/main", "a depth-1 clone of main carries origin/main"

    shallow_bundle = shallow / "docs" / "evidence" / "7949-capstone"
    report = check.run(shallow_bundle, use_git=True, use_corpus=False)
    assert report["ok"], report["failures"]
    assert report["ordering"] == check.HISTORY_UNAVAILABLE, report["ordering"]
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["ordering_base_candidate"] == "origin/main", report["ordering_base_candidate"]

    assert check.main(["--dir", str(shallow_bundle), "--no-corpus"]) == 0
    out = capsys.readouterr().out
    assert "ordering measured against: (not measured)" in out, out
    assert "would be measured against: origin/main" in out, out
    # ⛔ The pre-fix line, verbatim: a base named as measured when none was.
    assert "ordering measured against: origin/main" not in out, out


def test_an_uncommitted_registration_reports_no_measured_base(tmp_path, monkeypatch, capsys):
    """Follow-up 3 (review cycle 8): the RETURN arms measured nothing either, and said otherwise.

    The cycle-7 F2 fix closed the arm that RAISES (`HistoryUnavailable`): the base was no longer
    named as measured. It did not close the arms that RETURN early — `check_ordering` leaves before
    the per-log loop when the registration is not committed (`_is_tracked`) or when it is absent
    from the base, and `run()` then set `ordering = "verified"` plus `ordering_base = <resolved>`
    unconditionally. So the log printed `machine record run-order claim: verified` and
    `ordering measured against: origin/main` for a run that compared zero epochs — the same
    silent-green the field exists to remove, one polarity over.

    ⛔ The refusal below is CORRECT and stays: an unprovable ordering is not an ordering. What is
    asserted is only that the report does not claim to have measured one.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    _write_and_commit(repo, "s3-ablation.log", epoch=1_700_000_200)
    bundle = repo / "docs" / "evidence" / "7949-capstone"
    # The real document, so completeness/E4 pass and the ONLY finding is the uncommitted one.
    (bundle / check.PREREG_NAME).write_text((PREREG_DIR / check.PREREG_NAME).read_text())
    prereg = bundle / check.PREREG_NAME
    assert check.history_unavailable() is False, "the fixture must carry real history"
    assert check._is_tracked(prereg) is False, "the fixture's registration must be uncommitted"
    assert check.resolve_base_ref() == "origin/main", "a base DOES resolve here — that is the trap"

    report = check.run(bundle, use_git=True, use_corpus=False)
    assert any("not committed" in f for f in report["failures"]), report["failures"]
    assert report["ok"] is False, report
    # ⛔ The pre-fix values, verbatim: a base named and a claim verified for a run that read no epoch.
    assert report["ordering"].startswith(check.ORDERING_UNMEASURED), report["ordering"]
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["ordering_base_candidate"] == "origin/main", report["ordering_base_candidate"]

    assert check.main(["--dir", str(bundle), "--no-corpus"]) == 1
    out = capsys.readouterr().out
    assert "ordering measured against: (not measured)" in out, out
    assert "ordering measured against: origin/main" not in out, out
    assert f"machine record run-order claim: {check.ORDERING_UNMEASURED}" in out, out
    assert "would be measured against: origin/main" in out, out


def test_a_squash_collapsed_base_refuses_even_when_the_contribution_is_ordered(tmp_path, monkeypatch):
    """THE LOG-SIDE HALF of the squash-invariance rule (#10463 review cycle 7, finding 1).

    WHY THIS IS THE SHAPE. The rule reads each log's add-commit **on the base** when the log is
    already merged there, and in the contribution when it is not. That log-side read had NO witness:
    at the head that finding was made, forcing `base_log_epochs = []` (so every log is read from
    the contribution) left all 212 tests green, and on the real #10468 shape it flipped a REFUSAL
    into a silent PASS. The registration side was covered; the log side — half the predicate — was
    not. (That count is historical: the same mutation now reds three tests, this one among them.)

    The shape that discriminates: the base carries the registration and the log in ONE squash commit
    (equal epochs), while THIS contribution carries them in order. Reading the log from the
    contribution alone therefore ACCEPTS it; reading it from the base REFUSES it.

    ⛔ "Only a `_squashed_base` shape can discriminate" was FALSE (review cycle 8, finding 1): a base
    where the log predates the registration — two separate commits — discriminates just as well, and
    is witnessed by `test_a_base_where_the_log_predates_the_registration_refuses_an_ordered_contribution`
    below. Both are kept because they are different paths through the same base read.
    """
    repo = _repo(tmp_path, monkeypatch)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    # The contribution: registration first, log second — ORDERED, and provably so.
    prereg = _commit_prereg(
        repo, epoch=1_700_001_000, text=(PREREG_DIR / check.PREREG_NAME).read_text())
    log = _write_and_commit(repo, "capstone.log", epoch=1_700_002_000)
    # The base: the same two artefacts in ONE commit, on a ref that is NOT an ancestor of this branch.
    base_sha = _squashed_base(repo, epoch=1_700_001_500)
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    # The discrimination, asserted FIRST so the test states what it is about: the contribution's own
    # ordering is strict (log 1_700_002_000 > prereg 1_700_001_000), so a contribution-only reading
    # would pass this tree. It must not.
    assert min(check._added_epochs(log)) > min(check._added_epochs(prereg))

    failures = check.check_ordering(prereg, [log])
    assert failures, (
        "the log was accepted from the contribution's own ordering — the log-side base read is not "
        "load-bearing, which is the exact regression #10463 exists to prevent")
    assert "not strictly after" in failures[0], failures[0]
    # …and the squash-collapse diagnostic names the withdrawal, because that is the only remedy.
    assert "SAME commit" in failures[0] and "WITHDRAWAL" in failures[0], failures[0]


def test_a_base_where_the_log_predates_the_registration_refuses_an_ordered_contribution(
        tmp_path, monkeypatch):
    """The SECOND shape that discriminates the base reading from the PR-internal one.

    The squash-collapse shape (equal epochs on the base) is one; this is the other, and it needs no
    squash at all: on the base the log was added BEFORE the registration (two separate commits),
    while THIS contribution adds them in the correct order. A contribution-only reading accepts the
    tree (the ordering here is strict); the base reading refuses it, correctly — the log was already
    published when the registration was not.

    Measured by the reviewer as the counterexample to a `_squashed_base`-only claim (review cycle 8,
    finding 1). Kept because it exercises the log-side base read through axes the squash test does
    not: the epochs on the base are NOT equal, and there is NO shared add-commit (the log lands
    first, in its own commit, before the registration). ⛔ It does NOT differ by "no orphan branch" —
    an earlier version of this line said so and was false; both witnesses build their base on an
    orphan branch. A future change that special-cases the squash shape cannot pass both.
    """
    repo = _repo(tmp_path, monkeypatch)
    _git(repo, "remote", "add", "origin", "https://example.invalid/x.git")
    # The contribution: registration FIRST, log second — strictly ordered HERE.
    prereg = _commit_prereg(
        repo, epoch=1_700_001_000, text=(PREREG_DIR / check.PREREG_NAME).read_text())
    log = _write_and_commit(repo, "capstone.log", epoch=1_700_002_000)
    assert min(check._added_epochs(log)) > min(check._added_epochs(prereg)), (
        "the contribution must be ordered, or this test cannot discriminate")

    # The base: the LOG first (1_700_000_100), the registration later (1_700_000_300) — separate
    # commits, on a ref that is not an ancestor of this branch.
    branch = _git_out(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "base-order")
    _git(repo, "rm", "-rq", "--cached", "docs")
    # ⛔ The registration is still on DISK from the branch checkout, so `git add docs` would put it
    # in the log's commit — collapsing the two into one and measuring nothing (the first version of
    # this test did exactly that, and its own "no SAME commit" assertion caught it). Remove it first.
    (repo / "docs" / "evidence" / "7949-capstone" / check.PREREG_NAME).unlink()
    _stage_log(repo, "capstone.log")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "the log lands first", epoch=1_700_000_100)
    _write_prereg_uncommitted(repo)
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "the registration lands later", epoch=1_700_000_300)
    base_sha = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qf", branch)
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)

    failures = check.check_ordering(prereg, [log])
    assert failures, (
        "the log was accepted from the contribution's own ordering — the log-side base read is not "
        "load-bearing on the non-squash path either")
    assert "not strictly after" in failures[0], failures[0]
    # No squash collapse here, so the withdrawal diagnostic must NOT fire: the remedy is a proper
    # ordering, not a withdrawal.
    assert "SAME commit" not in failures[0], failures[0]


# ── cycle 12: "no pair was compared" is not "verified" ───────────────────────


def _bundle_with_published_registration(tmp_path, monkeypatch):
    """A repo whose registration is on `origin/main`, and the bundle directory.

    Two obligations of `run()` are irrelevant to what these tests measure — the corpus half and the
    machine record — so they are satisfied rather than exercised (see `_measurement_only`).
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    # The REAL document, so the completeness half passes and the only subject under test is the
    # ordering report. Its pins block is supplied by `_measurement_only` instead of read from the
    # file, because the real pins exempt a log these fixtures deliberately do not create.
    prereg = _commit_prereg(repo, epoch=1_700_000_100,
                            text=(PREREG_DIR / check.PREREG_NAME).read_text())
    _publish_base(repo)
    return repo, prereg.parent


def _measurement_only(monkeypatch):
    """Strip the halves that are not under test, so the ORDERING claim is the only variable."""
    monkeypatch.setattr(check, "parse_pins", lambda text: {"e4_denominator": 15})
    monkeypatch.setattr(check, "check_machine_record", lambda directory, pins: [])
    # …and the #10215 step-set arm, which is the THIRD subject these tests do not measure. It is
    # scoped by PATH (`check_step_partition_applies` asks whether the bundle IS `DEFAULT_PREREG_DIR`)
    # and these fixtures monkeypatch `REPO_ROOT` to a throwaway repo whose bundle sits at exactly
    # that relative path — so the arm applies here while the fixture's stubbed pins carry no `steps`
    # block, and it refuses with `pins.steps is empty` about a document it never reads. That arm has
    # its own tests in the `#10215` block below, which read the REAL record and document; stubbing it
    # here keeps each block's coverage in the block that owns it, instead of making every ordering
    # test depend on a step declaration the ordering rule does not consume.
    monkeypatch.setattr(check, "check_step_partition_applies", lambda directory, pins, record: False)
    # …and the ROW→FLOOR map the completeness half now binds its floor-clause exemption against
    # (#11331 review round 9). It is read from the machine record, which these fixtures
    # deliberately do not commit (that is what the `check_machine_record` stub above replaces), so
    # without this the ABSENT record reads as "this row has no pinned floor" — which is now a
    # REFUSAL of any comparator, and the REAL `A4` cell carries a legitimate one. Supplying the
    # real map is again "satisfied rather than exercised": no arm below measures floors, and the
    # floor binding has its own arms in `test_capstone_a_rows_10982.py`. Capturing the function
    # before patching is required — calling `check._registered_floor_values` from inside the
    # replacement would resolve to the replacement.
    _real_floor_values = check._registered_floor_values
    monkeypatch.setattr(check, "_registered_floor_values",
                        lambda directory: _real_floor_values(PREREG_DIR))


def test_the_gate_SUPPLIES_the_floor_map_so_a_missing_pin_cannot_excuse_a_bar(tmp_path, monkeypatch):
    """FAILS IF `check()` stops handing the floor map to `check_completeness`.

    ⛔ The fail-closed binding is only as good as its WIRING. `check()` passes
    `_registered_floor_values(directory)` into `check_completeness`, and reverting that one call to
    `check_completeness(rows)` leaves every other capstone arm GREEN — measured at #11331 review
    round 10 (`552 passed` either way), because with no map the checker takes the legacy
    strip-by-shape branch and the real `A4` cell still passes.

    So this drives the WHOLE gate with a map that OMITS `A4` — the state a record-only edit
    produces by deleting `criteria[A4].floor`. The real `A4` cell's legitimate
    `floor of >= 30 typed statements` can then no longer be told from a bar, so the gate must
    REFUSE. If the wiring is removed, `run()` returns ok and this test reds.
    """
    _repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    _measurement_only(monkeypatch)
    real = check._registered_floor_values(PREREG_DIR)
    assert real is not None and "A4" in real, real
    without_a4 = check.FloorMap(
        {k: v for k, v in real.items() if k != "A4"},
        {k: v for k, v in real.units.items() if k != "A4"},
    )
    monkeypatch.setattr(check, "_registered_floor_values", lambda directory: without_a4)

    report = check.run(bundle, use_git=True, use_corpus=False)
    assert report["ok"] is False, "the gate must refuse when A4's floor pin is missing"
    assert any("outside its floor" in f for f in report["failures"]), report["failures"]


def test_a_bundle_with_no_log_does_not_claim_a_measured_ordering(tmp_path, monkeypatch, capsys):
    """A registration on the base and ZERO logs: the loop compares nothing, so nothing was measured.

    ⛔ The `if not logs:` guard in `check_ordering` sits INSIDE `if not prereg_epochs:` — it fires
    only when the registration is ABSENT from the base. With the registration published and no
    evidence log, control reaches the per-log loop, the loop body never runs, `unmeasured` stayed
    empty, and `run()` therefore took the `else` branch: `ordering = "verified"` and
    `ordering_base = origin/main`, over zero comparisons. Measured on this PR's own CI command, half
    of it (the `docs/evidence/5087-hypothesis` bundle, after its log was withdrawn) printed

        evidence logs: 0
        machine record run-order claim: verified
        ordering measured against: origin/main

    That is the silent-green `ordering_base` exists to remove — the same lie the RETURN arms told,
    one step outside every one of them. A withdrawn log is an honest end state for a bundle, so the
    bundle must be able to say "there is nothing here to order" without saying "I measured it".
    """
    _repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    assert list(bundle.glob(check.EVIDENCE_LOG_GLOB)) == [], "the fixture must carry NO evidence log"
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"].startswith(check.ORDERING_UNMEASURED), report["ordering"]
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["ordering_base_candidate"] == "origin/main", report["ordering_base_candidate"]

    assert check.main(["--dir", str(bundle), "--no-corpus"]) == 0
    out = capsys.readouterr().out
    assert "ordering measured against: origin/main" not in out, out
    assert "ordering measured against: (not measured)" in out, out
    assert "would be measured against: origin/main" in out, out
    assert f"machine record run-order claim: {check.ORDERING_UNMEASURED}" in out, out


def test_a_bundle_whose_only_log_is_untracked_does_not_claim_a_measured_ordering(
        tmp_path, monkeypatch, capsys):
    """"Every log is untracked" is the other way to walk the loop and compare nothing.

    The untracked-log skip is CORRECT — an uncommitted file cannot predate a commit that already
    exists, and it re-enters the gate the moment it is committed. What was wrong was the report:
    the comparison counter never advanced, so the bundle was announced as measured. Same defect as
    the zero-log case, reached through the `continue` rather than through an empty list — which is
    why the counter is incremented where an epoch is ESTABLISHED, not derived from `len(logs)`.
    """
    _repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    scratch = bundle / "brand-new.log"
    scratch.write_text("x\n")  # written, never `git add`-ed
    assert check._is_tracked(scratch) is False, "the log must be untracked"
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"].startswith(check.ORDERING_UNMEASURED), report["ordering"]
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]

    assert check.main(["--dir", str(bundle), "--no-corpus"]) == 0
    out = capsys.readouterr().out
    assert "ordering measured against: origin/main" not in out, out
    assert "ordering measured against: (not measured)" in out, out


def test_one_comparable_pair_is_enough_to_report_the_bundle_as_measured(tmp_path, monkeypatch):
    """⛔ The counter is bundle-level on purpose: a measured pair is NOT reported as unmeasured.

    A per-log `unmeasured` append would report this bundle — one ordered log plus one untracked
    log, i.e. a REAL measurement — as `UNMEASURED`, which is the same class of lie in the other
    direction, and it would make the arm useless the first time a lane had a scratch log open.
    """
    repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    ordered = bundle / "a-ordered.log"
    ordered.write_text("x\n")
    _git(repo, "add", str(ordered.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "log after the registration", epoch=1_700_000_200)
    (bundle / "scratch.log").write_text("x\n")  # untracked, and irrelevant
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"] == "verified", report["ordering"]
    assert report["ordering_base"] == "origin/main", report["ordering_base"]


def test_the_armed_rule_does_not_claim_a_measured_ordering_when_it_compared_nothing(
        tmp_path, monkeypatch, capsys):
    """The ARMED rule had no comparison counter, so "I compared nothing" read as `verified`.

    ⛔ ISSUE 1 of the review at `01d9ce879`. `_check_ordering_proposed` had neither a `compared`
    counter nor an `unmeasured` out-param (the ruled path has had both since #10463 cycle 12), so a
    bundle whose only log was WITHDRAWN — the honest end state this file already tests on the ruled
    path, two tests up — walked zero pairs, returned `[]`, and the armed `else` handed it
    `ordering: verified` / `run_order_claim: verified` / `ordering_base: PROPOSED_ORDERING_BASE`.
    `run_order_claim` is the field the module says a machine consumer reads by name, so the one
    path this PR adds was the one path that had lost the guard its sibling gained.

    Non-vacuity: this assertion cannot pass on the pre-fix tree — there the report says `verified`
    over an empty loop (mutation: delete `compared += 1` in `_check_ordering_proposed` and this
    test reds the same way, because the counter is the only thing that reaches the arm).
    """
    _repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    assert list(bundle.glob(check.EVIDENCE_LOG_GLOB)) == [], "the fixture must carry NO evidence log"
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False, allow_atomic_disclosure=True)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"].startswith(check.ORDERING_UNMEASURED), report["ordering"]
    # …and the base is NOT claimed either: naming the proposed base over zero comparisons is the
    # same silent green, one field over (#10548 r15 F3's mirror).
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["run_order_claim"].startswith(check.ORDERING_UNMEASURED), report["run_order_claim"]

    assert check.main(["--dir", str(bundle), "--no-corpus", "--allow-atomic-disclosure"]) == 0
    out = capsys.readouterr().out
    assert f"machine record run-order claim: {check.ORDERING_UNMEASURED}" in out, out
    assert check.PROPOSED_ORDERING_BASE not in out, out


def test_the_armed_rule_reports_an_uncommitted_registration_and_no_log_as_unmeasured(
        tmp_path, monkeypatch, capsys):
    """⛔ ISSUE 1 of the review at `43b6a3952` (BLOCKING): the counter never saw the EARLY RETURN.

    `_check_ordering_proposed` returned from `if not prereg_adds: if not logs: return failures`
    ABOVE `compared = 0`, so the bundle-level guard at the end of the same function could not run:
    a full-history checkout whose registration exists but is UNCOMMITTED, with no evidence log,
    returned `[]` with `unmeasured == []`, and `run()`'s armed `else` therefore reported
    `ordering: verified` / `run_order_claim: verified` / `ordering_base: PROPOSED_ORDERING_BASE`
    over ZERO comparisons — the very "dressed `decided nothing` as `verified`" shape this PR claims
    to have ended, surviving on one return.

    ⛔ AND THE TWO RULES DISAGREE ON A STATE BOTH MUST DESCRIBE. The ruled path answers the same
    tree with `unmeasured` ("no registration on the base and no evidence log to order"); the armed
    path is asked here through the SAME dispatcher on the SAME tree, and the assertion is that both
    report that they compared nothing and neither prints a measured claim. That is the property a
    reader needs: whichever rule is in force, "nothing was compared" is not "verified".

    Non-vacuity: this test reds on the pre-fix tree at every one of the three armed assertions (no
    `unmeasured` append on the return ⇒ `verified`), and it reds on the ruled half if the sibling's
    own append is removed — the mutation the earlier cycle already used for the ruled path.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    bundle = repo / "docs" / "evidence" / "7949-capstone"
    bundle.mkdir(parents=True)
    # The REAL document, written and NEVER committed: this is the state a lane is in before it
    # commits anything, and `git log --diff-filter=A` finds no add-commit for it anywhere.
    prereg = bundle / check.PREREG_NAME
    prereg.write_text((PREREG_DIR / check.PREREG_NAME).read_text())
    assert list(bundle.glob(check.EVIDENCE_LOG_GLOB)) == [], "the fixture must carry NO evidence log"
    assert check._added_commits(prereg) == [], "premise: the registration has NO add-commit here"
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False, allow_atomic_disclosure=True)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"].startswith(check.ORDERING_UNMEASURED), report["ordering"]
    assert report["ordering_base"] == "(not measured)", report["ordering_base"]
    assert report["run_order_claim"].startswith(check.ORDERING_UNMEASURED), report["run_order_claim"]

    assert check.main(["--dir", str(bundle), "--no-corpus", "--allow-atomic-disclosure"]) == 0
    out = capsys.readouterr().out
    assert f"machine record run-order claim: {check.ORDERING_UNMEASURED}" in out, out
    assert check.PROPOSED_ORDERING_BASE not in out, out

    # ⛔ SAME TREE, BOTH RULES: the two invariants must AGREE that nothing was compared. An armed
    # rule that says `verified` where the ruled one says `UNMEASURED` is a rule that has lost the
    # guard, not a rule with a different opinion about the base.
    ruled_unmeasured: list[str] = []
    armed_unmeasured: list[str] = []
    ruled = check.check_ordering(prereg, [], {}, unmeasured=ruled_unmeasured)
    armed = check.check_ordering(prereg, [], {}, allow_atomic_disclosure=True,
                                unmeasured=armed_unmeasured)
    assert ruled == [] and armed == [], (ruled, armed)
    assert ruled_unmeasured, "the ruled path already reported this state — the armed one must too"
    assert armed_unmeasured, (
        "the armed path walked zero pairs and said nothing about it, so run()'s armed `else` "
        "hands it `verified`")


def test_the_armed_rule_still_reports_a_pair_it_did_order_as_measured(tmp_path, monkeypatch):
    """The mirror of the untracked-log test, on the armed path: a REAL measurement stays `verified`.

    A per-log `unmeasured` append would report this bundle — one orderable log beside one untracked
    scratch file — as UNMEASURED, which is the same class of lie in the other direction. The counter
    is bundle-level on both paths, and the armed path must not have bought its new guard by
    refusing to ever say `verified`.
    """
    repo, bundle = _bundle_with_published_registration(tmp_path, monkeypatch)
    ordered = bundle / "a-ordered.log"
    ordered.write_text("x\n")
    _git(repo, "add", str(ordered.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "log after the registration", epoch=1_700_000_200)
    (bundle / "scratch.log").write_text("x\n")  # untracked, and irrelevant
    _measurement_only(monkeypatch)

    report = check.run(bundle, use_git=True, use_corpus=False, allow_atomic_disclosure=True)
    assert report["ok"] is True, report["failures"]
    assert report["ordering"] == "verified", report["ordering"]
    assert report["ordering_base"] == check.PROPOSED_ORDERING_BASE, report["ordering_base"]


# ── cycle 13: the readers must see an add hidden behind a merge ───────────────


def test_the_ordering_readers_see_an_add_hidden_behind_a_merge(tmp_path, monkeypatch):
    """FAIL-OPEN, measured (review cycle 13, finding 1): simplification can hide the EARLIER add.

    `git log --diff-filter=A` follows only the TREESAME parent at a merge. So when one branch adds
    the log at T1 (before the registration) and a sibling adds the same path at T2 (after it), and
    the merge is resolved to the sibling's copy, the default read returns **[T2] only** — `min()`
    is then later than the truth and a log that predates the registration is ACCEPTED. That is
    precisely the fabricated ordering the gate exists to refuse, passing silently.

    The file already knew this hazard: `_touch_epochs` carries `--full-history` for it, with a
    comment saying git's simplification can drop a merge commit that touched the log, and a test
    (`test_the_touch_history_sees_a_merge_commit`). The lesson had been applied to a DIAGNOSTIC
    and not to the two readers that decide the VERDICT — which is exactly why it went unnoticed.

    The fixture is the criss-cross shape, and the assertion is on the ACCEPT it used to produce.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    prereg = _commit_prereg(repo, epoch=1_700_003_000)
    _publish_base(repo)
    log = prereg.parent / "capstone.log"

    # Branch `early`: the log lands BEFORE the registration was published.
    _git(repo, "checkout", "-q", "-b", "early")
    log.write_text("measured before the thresholds\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "log first", epoch=1_700_000_100)

    # Branch `late`: the same path, after the registration, and the merge keeps THIS copy. It
    # branches from the PUBLISHED registration commit, not from `early`.
    _git(repo, "checkout", "-q", "-b", "late", "origin/main")
    # Leaving `early` removed the now-empty directory, so re-create it before writing the copy.
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("measured after the thresholds\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "log again", epoch=1_700_004_000)
    _git(repo, "merge", "-q", "-s", "ours", "-m", "merge early, keep late's copy", "early",
         epoch=1_700_005_000)

    # The premise: the DEFAULT read is blind to the earlier add, `--full-history` is not.
    default_epochs = [int(x) for x in _git_out(
        repo, "log", "--diff-filter=A", "--format=%ct", "--", "docs/evidence/7949-capstone/"
        "capstone.log").split()]
    full_epochs = [int(x) for x in _git_out(
        repo, "log", "--full-history", "--diff-filter=A", "--format=%ct", "--",
        "docs/evidence/7949-capstone/capstone.log").split()]
    assert min(default_epochs) == 1_700_004_000, default_epochs
    assert min(full_epochs) == 1_700_000_100, full_epochs

    failures = check.check_ordering(prereg, [log], {})
    assert failures, (
        "a log whose EARLIER add is hidden behind a merge was accepted — the ordering readers are "
        "history-simplified again, and `min()` is then later than the truth")
    assert "not strictly after" in failures[0], failures[0]


def test_an_ordering_violation_is_not_reported_as_a_verified_claim(tmp_path, monkeypatch, capsys):
    """A VIOLATED ordering must not print `run-order claim: verified` (review cycle 13, finding 2).

    `run()` took `ordering = "verified"` whenever `unmeasured` was empty, without looking at what
    `check_ordering` returned. So on a tree with two "not strictly after" failures — a real
    violation — the two fields a consumer keys on reported the opposite of the measurement, while
    `ok`/exit were correctly 1. The exit code saved it, and that is exactly why it survived: every
    test of the report ran on a PASSING tree.

    This test also pins the BASE on the same tree (#10548 r15 F3): the verdict went to `failed`,
    but the base stayed `(not measured)` because it was assigned only on the `verified` path — so
    the log said no measurement happened over a pair it had just refused at a named ref. Both
    halves are measured here on the SAME fixture, which is the only shape that can catch a fix that
    reports one without the other.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    prereg = _commit_prereg(repo, epoch=1_700_003_000,
                            text=(PREREG_DIR / check.PREREG_NAME).read_text())
    _publish_base(repo)
    # ⛔ The completeness half's floor binding reads the machine record, and this fixture commits
    # only the DOCUMENT. Without the map, the ABSENT record means "A4 has no pinned floor", so the
    # real A4 cell's legitimate floor clause is refused and its `UNMEASURED(...)` text lands in the
    # output this test scans for an UNMEASURED *ordering* outcome — a red about a half the test does
    # not measure (#11331 review round 9). Same "satisfied rather than exercised" shape as
    # `_measurement_only`; the floor binding has its own arms in `test_capstone_a_rows_10982.py`.
    _real_floor_values = check._registered_floor_values
    monkeypatch.setattr(check, "_registered_floor_values",
                        lambda directory: _real_floor_values(PREREG_DIR))
    log = prereg.parent / "capstone.log"
    log.write_text("x\n")
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "log BEFORE the registration", epoch=1_700_000_100)
    monkeypatch.setattr(check, "check_machine_record", lambda directory, pins: [])

    report = check.run(prereg.parent, use_git=True, use_corpus=False)
    assert report["ok"] is False, report
    assert any("not strictly after" in f for f in report["failures"]), report["failures"]
    assert report["ordering"] != "verified", report["ordering"]
    assert report["run_order_claim"] != "verified", report["run_order_claim"]
    # ⛔ A VIOLATED pair WAS measured, so the field named for the measurement carries the ref that
    # refused it (#10548 r15 F3). Pre-fix this asserted `"(not measured)"`, which put the log in
    # the position of saying no comparison happened while the failure lines two rows above it
    # named `origin/main` as the ref the pair was refused against — and two documents now tell a
    # reader to trust precisely this line. `ordering` already carries the VERDICT (`failed`); the
    # base may not be made to carry it a second time.
    assert report["ordering_base"] == "origin/main", report["ordering_base"]
    assert report["ordering_base_candidate"] == "origin/main", report["ordering_base_candidate"]

    assert check.main(["--dir", str(prereg.parent), "--no-corpus"]) == 1
    out = capsys.readouterr().out
    assert "machine record run-order claim: verified" not in out, out
    assert check.ORDERING_UNMEASURED not in out, ("a VIOLATION is not an UNMEASURED outcome — it "
                                                  "was measured, and it failed")
    # The line a human reads must name the ref, and must not ALSO offer it as a mere intention.
    assert "ordering measured against: origin/main" in out, out
    assert "would be measured against" not in out, (
        "a violated ordering WAS measured against a base, so offering that same ref as \"would "
        f"be measured against\" contradicts the line directly above it: {out}"
    )


def test_the_base_read_sees_an_add_hidden_behind_a_merge(tmp_path, monkeypatch):
    """T3 on the BASE side: the flag that makes the base read honest had NO witness.

    `test_the_ordering_readers_see_an_add_hidden_behind_a_merge` publishes the base BEFORE the log
    exists, so `base_log_epochs == []` and the epoch under test comes from `_added_epochs` — the
    contribution reader. Deleting `--full-history` from `_added_epochs_on` therefore changed
    nothing: the whole file stayed green while the base reader could accept a log whose earlier add
    the merge hid. That is the half this PR's squash-invariance argument rests on, so it gets its
    own fixture: the criss-cross merge is what the BASE carries, and the contribution adds nothing.

    Measured both ways: with the flag the ordering is REFUSED at the hidden epoch; without it the
    same tree is ACCEPTED, silently.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    rel = "docs/evidence/7949-capstone/capstone.log"
    log = repo / rel
    log.parent.mkdir(parents=True, exist_ok=True)

    # `early` adds the log at 1_700_000_100; `late` adds the same path at 1_700_000_400; the merge
    # into `late` keeps late's copy, so git's default walk sees only the later add.
    _git(repo, "checkout", "-q", "-b", "early")
    log.write_text("measured before the thresholds\n")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "log first", epoch=1_700_000_100)
    _git(repo, "checkout", "-q", "-b", "late", "origin/main")
    # Leaving `early` removed the now-empty directory, so re-create it before writing the copy.
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("measured after the thresholds\n")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "log again", epoch=1_700_000_400)
    _git(repo, "merge", "-q", "-s", "ours", "-m", "merge early, keep late's copy", "early",
         epoch=1_700_000_500)

    # The registration lands on the base AFTER that merge, so BOTH artefacts are published there
    # and the comparison is a base-side one — which is the reader under test.
    prereg = _commit_prereg(repo, epoch=1_700_000_300)
    _publish_base(repo)

    default_epochs = [int(x) for x in _git_out(
        repo, "log", "--diff-filter=A", "--format=%ct", "origin/main", "--", rel).split()]
    full_epochs = [int(x) for x in _git_out(
        repo, "log", "--full-history", "--diff-filter=A", "--format=%ct", "origin/main", "--",
        rel).split()]
    assert min(default_epochs) == 1_700_000_400, default_epochs
    assert min(full_epochs) == 1_700_000_100, full_epochs
    assert check._added_epochs_on("origin/main", log) == sorted(full_epochs), (
        "the BASE reader must see every add the merge hid, not just the surviving copy")

    failures = check.check_ordering(prereg, [log], {})
    assert failures, (
        "a log whose earlier add on the BASE is hidden behind a merge was accepted — the base "
        "reader is history-simplified, and `min()` is then later than the truth")
    assert "not strictly after" in failures[0], failures[0]


def test_the_squash_diagnosis_sees_a_collapsed_add_hidden_behind_a_merge(tmp_path, monkeypatch):
    """F2: the SQUASH diagnosis reads history too, and it was the one reader left simplified.

    `_add_commits_on` decides no epoch, so `--full-history` was never added to it — but it decides
    WHICH refusal an operator reads, because `shared = prereg_add_commits & _add_commits_on(base,
    log)` gates `SQUASH_COLLAPSE_REMEDY`: the only refusal text that names a withdrawal. Where the
    merge hid the collapsed commit, `shared` was empty and the operator got the bare "not strictly
    after" — the defect this same PR fixed as cycle-3 finding 1c, reintroduced on the one shape
    whose remedy is a withdrawal.

    The fixture is the criss-cross shape with BOTH artefacts in the collapsed commit: `early` adds
    the registration and the log in ONE commit, `late` adds the same log path later, and the merge
    (`-X ours`) resolves the log conflict to `late`'s copy while keeping the registration from
    `early`. That asymmetry is what makes the two readers disagree: the log path is TREESAME to
    `late` and its earlier add is pruned, while the registration path is TREESAME to `early` and
    is walked — so without the flag `shared` is empty and the remedy is absent, even though
    `_added_epochs_on` (which has the flag) correctly refuses at the collapsed epoch.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    rel_prereg = "docs/evidence/7949-capstone/" + check.PREREG_NAME
    rel_log = "docs/evidence/7949-capstone/capstone.log"
    prereg = repo / rel_prereg
    log = repo / rel_log
    prereg.parent.mkdir(parents=True, exist_ok=True)

    # `early`: the registration AND its log in ONE commit — the squash-collapse shape.
    _git(repo, "checkout", "-q", "-b", "early")
    prereg.write_text("x\n")
    log.write_text("measured before the thresholds\n")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "registration and log in one commit", epoch=1_700_000_100)
    collapsed = _git_out(repo, "rev-parse", "HEAD")

    # `late`: the same log path, later, and the merge keeps THIS copy for that path.
    _git(repo, "checkout", "-q", "-b", "late", "origin/main")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("measured after the thresholds\n")
    _git(repo, "add", rel_log)
    _git(repo, "commit", "-q", "-m", "log again", epoch=1_700_000_400)
    # `-X ours`, not `-s ours`: a real merge keeps the registration (only `early` has it) while
    # resolving the add/add log conflict to `late`'s copy.
    _git(repo, "merge", "-q", "-X", "ours", "-m", "merge early, keep late's log", "early",
         epoch=1_700_000_500)
    _publish_base(repo)

    # Premise, measured on the raw walks: the log path loses the collapsed commit to
    # simplification, while the registration path keeps it.
    default_log = _git_out(
        repo, "log", "--diff-filter=A", "--format=%H", "origin/main", "--", rel_log).split()
    full_log = _git_out(
        repo, "log", "--full-history", "--diff-filter=A", "--format=%H", "origin/main", "--",
        rel_log).split()
    assert collapsed not in default_log, (
        "the fixture no longer hides the collapsed add from the default walk — it would not "
        f"discriminate: {default_log}")
    assert collapsed in full_log, full_log
    assert collapsed in _git_out(
        repo, "log", "--diff-filter=A", "--format=%H", "origin/main", "--", rel_prereg).split(), (
        "the registration path must still be WALKED by the default read — the two readers have to "
        "disagree for this test to witness the flag")

    failures = check.check_ordering(prereg, [log], {})
    assert failures, "an equal-epoch base must still be refused"
    assert "not strictly after" in failures[0], failures[0]
    assert "WITHDRAWAL plus a NEW artefact" in failures[0], (
        "the refusal lost the squash-collapse remedy because the diagnosis reader is "
        "history-simplified (`_add_commits_on` without `--full-history`) — the operator then gets "
        f"a bare 'not strictly after' and hunts an ordering that does not exist: {failures[0]}"
    )
    assert collapsed[:12] in failures[0], (
        "the remedy must name the collapsed add-commit, which the diagnosis reader can only see "
        f"with `--full-history`: {failures[0]}"
    )


def test_the_collapse_is_named_by_epoch_not_by_the_sha_text(tmp_path, monkeypatch):
    """F2 (nit): `sorted(shared)[0]` sorted HEX STRINGS, so the sha the remedy named was arbitrary.

    `shared` is a set of add-commits, and the remedy names one of them as "the SAME commit
    ({sha})" that collapsed the pair. When a bundle has more than one shared add-commit — a
    collapsed pair that was withdrawn and re-added is still measured against BOTH, because a
    deletion does not remove an earlier add — `sorted(shared)[0]` picks the lexicographically
    smallest sha, which is unrelated to which commit is the collapse. The operator is then sent to
    look at the wrong commit. The collapsed EPOCH is the fact the refusal is about, so the sha is
    selected by epoch.

    The fixture forces the discriminating case deterministically: two shared add-commits whose
    hex order is INVERTED relative to their epochs (the later commit sorts first), which is the
    only shape on which a sha sort and an epoch sort disagree.
    """
    repo = _repo_with_published_base(tmp_path, monkeypatch)
    rel_prereg = "docs/evidence/7949-capstone/" + check.PREREG_NAME
    rel_log = "docs/evidence/7949-capstone/capstone.log"
    prereg = repo / rel_prereg
    log = repo / rel_log
    prereg.parent.mkdir(parents=True, exist_ok=True)

    # The first collapsed pair: the EARLIEST add, and the one the remedy must name.
    prereg.write_text("x\n")
    log.write_text("measured\n")
    _git(repo, "add", "docs")
    _git(repo, "commit", "-q", "-m", "collapse one", epoch=1_700_000_100)
    earliest = _git_out(repo, "rev-parse", "HEAD")

    # Withdrawn, then re-added as a second collapsed pair at a LATER epoch. The nonce is varied
    # until this commit's sha sorts BEFORE the earliest one — the inversion that makes a sha sort
    # name the wrong commit. Deterministic: the same nonce is found on every run.
    _git(repo, "rm", "-rq", "docs")
    _git(repo, "commit", "-q", "-m", "withdraw the pair", epoch=1_700_000_200)
    later = ""
    for nonce in range(200):
        prereg.parent.mkdir(parents=True, exist_ok=True)
        prereg.write_text("x\n")
        log.write_text(f"measured again {nonce}\n")
        _git(repo, "add", "docs")
        _git(repo, "commit", "-q", "-m", f"collapse two {nonce}", epoch=1_700_000_300)
        later = _git_out(repo, "rev-parse", "HEAD")
        if later < earliest:
            break
        _git(repo, "reset", "-q", "--hard", "HEAD~1")
        _git(repo, "clean", "-qfd", "docs")
    assert later < earliest, (
        "could not build the inverted-sha fixture — the two commits must differ in hex order for "
        "this test to tell a sha sort from an epoch sort")
    _publish_base(repo)

    # Premise: BOTH adds are shared, and only their epochs order them.
    assert set(check._add_commits_on("origin/main", log)) == {earliest, later}
    assert check._added_epochs_on("origin/main", log) == [1_700_000_100, 1_700_000_300]

    failures = check.check_ordering(prereg, [log], {})
    assert failures, "an equal-epoch base must still be refused"
    assert earliest[:12] in failures[0], (
        "the remedy must name the commit that actually collapsed the pair — the EARLIEST "
        f"add-commit — not the sha that happens to sort first: {failures[0]}")
    assert later[:12] not in failures[0], (
        "the remedy named the LATER add-commit because `shared` was sorted as hex text rather "
        f"than by epoch — the operator is sent to the wrong commit: {failures[0]}")


# ── #10215: the step set the capstone's gate reads ────────────────────────────
#
# V5 (`tools/capstone_verdict_check.py`) and G5 (`probes/capstone_run.py`) read
# the REQUIRED step set out of the frozen record, so this block decides whether
# a step is required. A silent edit here is a silent weakening of the epic's own
# gate — which is why the split is checked from both sides and against §4.1's
# eight ids rather than against itself.


def _pins_with_steps(**steps) -> dict:
    pins = check.parse_pins(PREREG.read_text())
    block = dict(pins["steps"])
    block.update(steps)
    pins = dict(pins, steps=block)
    return pins


def _record_with_steps(**steps) -> dict:
    rec = _record()
    rec["steps"] = dict(check.parse_pins(PREREG.read_text())["steps"])
    rec["steps"].update(steps)
    return rec


def test_the_real_step_declaration_is_a_partition_of_the_eight():
    """The shipped document + record split §4.1's eight, and they agree."""
    pins = check.parse_pins(PREREG.read_text())
    record = json.loads((PREREG_DIR / check.MACHINE_RECORD).read_text())
    assert check.check_step_partition(pins, record) == []
    declared = set(pins["steps"]["unconditional"]) | {
        e["step"] for e in pins["steps"]["conditional"]}
    assert declared == set(check.CAPSTONE_STEPS)


def test_the_conditional_step_is_s2_3_and_says_when_it_does_not_apply():
    """The declaration is a CONDITION, not a waiver: it names why, how it is witnessed, and where
    the measurement lives. Removing any of the three leaves an assertion nobody can check."""
    entry = check.parse_pins(PREREG.read_text())["steps"]["conditional"][0]
    assert entry["step"] == "S2.3"
    for field in check.CONDITIONAL_STEP_FIELDS:
        assert str(entry[field]).strip(), field
    assert check._issue_number(entry["evidence_issue"]) == 10215
    # and the reason it is conditional is the collector's item set, cited in place
    assert "_collect_classify_items" in entry["condition"]
    assert "enabled=true" in entry["idle_evidence"]


def test_a_step_dropped_from_both_lists_fails():
    pins = _pins_with_steps(conditional=[])
    failures = check.check_step_partition(pins, _record_with_steps(conditional=[]))
    assert any("drops step(s) ['S2.3']" in f for f in failures), failures


def test_a_step_declared_both_ways_fails():
    pins = check.parse_pins(PREREG.read_text())
    pins["steps"]["unconditional"] = [*pins["steps"]["unconditional"], "S2.3"]
    failures = check.check_step_partition(pins, _record_with_steps())
    assert any("BOTH unconditional and conditional" in f for f in failures), failures


def test_a_step_id_outside_the_eight_fails():
    pins = _pins_with_steps(
        unconditional=[*check.CAPSTONE_STEPS[:3], "S4"])
    failures = check.check_step_partition(pins, _record_with_steps())
    assert any("not part of the epic's eight" in f for f in failures), failures


def test_a_conditional_entry_without_its_evidence_fails():
    pins = check.parse_pins(PREREG.read_text())
    entry = dict(pins["steps"]["conditional"][0])
    entry.pop("idle_evidence")
    pins["steps"]["conditional"] = [entry]
    failures = check.check_step_partition(pins, _record_with_steps())
    assert any("omits 'idle_evidence'" in f for f in failures), failures


def test_a_document_without_the_steps_block_fails():
    """No declaration means the required step set can change without a reviewable diff."""
    pins = dict(check.parse_pins(PREREG.read_text()))
    pins.pop("steps")
    failures = check.check_step_partition(pins, _record_with_steps())
    assert any("pins.steps is empty" in f for f in failures), failures


def test_a_record_that_diverges_from_the_document_fails():
    """The probe freezes the RECORD, so a divergence means the gate enforces something the
    argument never argued."""
    rec = _record_with_steps()
    rec["steps"]["unconditional"] = ["S0", "S1", *rec["steps"]["unconditional"]]
    failures = check.check_step_partition(check.parse_pins(PREREG.read_text()), rec)
    assert any("steps.unconditional" in f and "can disagree" in f for f in failures), failures


def test_a_conditional_step_without_a_record_block_fails():
    rec = _record()
    failures = check.check_step_partition(check.parse_pins(PREREG.read_text()), rec)
    assert any("carries no steps block" in f for f in failures), failures


# The RECORD's producer is another lane, so its SHAPE is untrusted input — the module's own
# contract (`_mapping`: "never a bare dict()/.get() crash"). A truthy wrong type on the RECORD
# half used to raise `TypeError` out of `check_step_partition` ("'int' object is not iterable"),
# past `run()` (which catches nothing) to `main()` (which catches only CheckError) — escaping as
# a traceback with exit **1**, the code this tool reserves for FINDINGS. The pins half already
# refused the same shape with CheckError (exit 2); these two arms hold the record half to it.
# Both are `raise`-based, so they fail on the pre-fix code as TypeError, not as a mismatch.
@pytest.mark.parametrize("field, value", [("unconditional", 5), ("conditional", 1)])
def test_a_wrong_typed_record_steps_field_is_a_parse_error_not_a_crash(field, value):
    rec = _record_with_steps()
    rec["steps"][field] = value
    with pytest.raises(check.CheckError) as excinfo:
        check.check_step_partition(check.parse_pins(PREREG.read_text()), rec)
    assert field in str(excinfo.value), excinfo.value
    assert "expected a list" in str(excinfo.value), excinfo.value


def test_the_wrong_typed_record_field_exits_2_and_says_why(tmp_path, capsys):
    """End to end: the CLI must report a PARSE error (exit 2), not a traceback with exit 1.

    This is the property the finding is about — the exit CODE is the tool's contract, and an
    uncaught exception borrows the one reserved for "the table has findings".
    """
    directory = _materialise(tmp_path, _record_with_steps(unconditional=5))
    code = check.main(["--dir", str(directory), "--no-git", "--no-corpus", "--json"])
    out = capsys.readouterr()
    assert code == 2, out.err
    # ⛔ The WHOLE payload, not its last line: `#10468` made the error branch pretty-print
    # (`indent=2`), so `splitlines()[-1]` is `}` and this assertion parsed nothing. The property
    # under test is the exit CODE and that the message names the field — never the line shape.
    body = json.loads(out.out)
    assert body["ok"] is False
    assert "unconditional" in body["error"]


# ── the steps pin's AUTHORING ORDER (#10215) ─────────────────────────────────
#
# ⛔ These build a REAL throwaway git repo (`_repo` rebinds `check.REPO_ROOT`),
# because the predicate is history. A `tmp_path` bundle never executes the order
# arm at all — measured at the round-6 review: deleting the whole order branch
# left every test green, which is the vacuity this section exists to remove.

DEV_DOC_WITHOUT = "# Pre-registration\n\nNo deviation section here.\n"
DEV_DOC_WITH = ("# Pre-registration\n\n## Deviations\n\n"
                "### D1 — 2026-11-01 — the step set\n\n"
                "- **Original rule**: all eight observed.\n"
                "- **New rule**: seven required + S2.3 conditional.\n"
                "- **Reason**: the old rule was unsatisfiable.\n")


def _dev_repo(tmp_path, monkeypatch, *, steps_first: bool, doc: str = DEV_DOC_WITHOUT,
              already_pinned: bool = False, null_first: bool = False):
    """A throwaway repo holding a record, a scored log, and the argument."""
    repo = _repo(tmp_path, monkeypatch)
    bundle = repo / "docs" / "evidence" / "7949-capstone"
    bundle.mkdir(parents=True)

    def write_record(*, with_steps: bool, null_steps: bool = False,
                     unconditional: list[str] | None = None) -> None:
        record = {"schema": check.PREREG_SCHEMA,
                  "written_before_any_scored_run": True,
                  "written_before_any_scored_run_basis": f"no SCORED run; {check.UNSCORED}",
                  "document": "PRE-REGISTRATION.md"}
        if null_steps:
            record["steps"] = None
        elif with_steps:
            record["steps"] = {
                "unconditional": unconditional or ["S0"],
                "conditional": [{"step": "S2.3", "condition": "c"}]}
        (bundle / check.MACHINE_RECORD).write_text(json.dumps(record, indent=2) + "\n")

    def commit(paths, message: str, epoch: int) -> None:
        for p in paths:
            _git(repo, "add", str(p.relative_to(repo)))
        _git(repo, "commit", "-q", "-m", message, epoch=epoch)

    (bundle / check.PREREG_NAME).write_text(doc)
    record_path = bundle / check.MACHINE_RECORD
    log_path = bundle / "capstone-measured-e0fc940b.log"
    log_path.write_text("# generated: 2026-10-23T23:16:55Z\n# result: COMPLETED\n")

    if already_pinned:
        # The block was pinned BEFORE the run and EDITED after it: a key-presence
        # test dates this to the first commit and stays silent, which is the shape
        # round 6 filed and round 7 found untested.
        write_record(with_steps=True)
        commit([bundle / check.PREREG_NAME, record_path], "freeze the step set", 1_700_000_000)
        commit([log_path], "the scored measurement", 1_700_000_100)
        write_record(with_steps=True, unconditional=["S0", "S1"])
        commit([record_path], "widen the rule after the number", 1_700_000_200)
    elif null_first:
        # `"steps": null` in the definitionally pre-scored commit, filled in later:
        # `"steps" in record` is True at commit 1, so a key test stays green forever.
        write_record(with_steps=False, null_steps=True)
        commit([bundle / check.PREREG_NAME, record_path], "freeze the record", 1_700_000_000)
        commit([log_path], "the scored measurement", 1_700_000_100)
        write_record(with_steps=True)
        commit([record_path], "fill the step set after the number", 1_700_000_200)
    elif steps_first:
        write_record(with_steps=True)
        commit([bundle / check.PREREG_NAME, record_path], "freeze the step set", 1_700_000_000)
        commit([log_path], "the scored measurement", 1_700_000_100)
    else:
        write_record(with_steps=False)
        commit([bundle / check.PREREG_NAME, record_path], "freeze the record", 1_700_000_000)
        commit([log_path], "the scored measurement", 1_700_000_100)
        write_record(with_steps=True)
        commit([record_path], "pin the step set AFTER the number", 1_700_000_200)
    return bundle


def test_a_steps_pin_authored_AFTER_a_scored_log_without_a_deviation_reds(tmp_path, monkeypatch):
    """The finding the rule exists for — and the one no earlier test could produce."""
    bundle = _dev_repo(tmp_path, monkeypatch, steps_first=False)
    failures = check.check_steps_block_is_pre_scored(bundle)
    assert failures, "a rule pinned after the number must not pass silently"
    assert "were added" in failures[0] and "## Deviations" in failures[0], failures


def test_a_steps_pin_authored_BEFORE_a_scored_log_passes(tmp_path, monkeypatch):
    """The honest lifecycle: freeze the rule, then measure. Must not be refused."""
    assert check.check_steps_block_is_pre_scored(
        _dev_repo(tmp_path, monkeypatch, steps_first=True)) == []


def test_the_same_post_number_pin_passes_once_the_deviation_is_RECORDED(tmp_path, monkeypatch):
    """§8's actual requirement: the fix for the finding is the ENTRY, not a quieter log."""
    assert check.check_steps_block_is_pre_scored(
        _dev_repo(tmp_path, monkeypatch, steps_first=False, doc=DEV_DOC_WITH)) == []


def test_a_bare_deviations_HEADING_does_not_clear_the_finding(tmp_path, monkeypatch):
    """A heading with nothing under it discloses nothing — the "add one line" exit."""
    bare = "# Pre-registration\n\n## Deviations\n\n- **todo**\n"
    failures = check.check_steps_block_is_pre_scored(
        _dev_repo(tmp_path, monkeypatch, steps_first=False, doc=bare))
    assert failures, "an empty Deviations section must not satisfy §8"


def test_an_EDITED_pin_reports_the_EDIT_epoch_not_the_pin_epoch(tmp_path, monkeypatch):
    """The shape round 6 filed and round 7 found untested.

    The block was pinned BEFORE the run and EDITED after it. A key-presence test
    (`"steps" in record`) dates it to the first commit and stays green; the content
    compare must return the EDIT's epoch, so the failure must quote the gap to the
    scored-log epoch (100s), not to the original pin (200s).
    """
    bundle = _dev_repo(tmp_path, monkeypatch, steps_first=True, already_pinned=True)
    failures = check.check_steps_block_is_pre_scored(bundle)
    assert failures, "an edited rule after the number must not pass silently"
    assert "were added" in failures[0], failures
    assert "100s AFTER" in failures[0], failures


def test_a_null_steps_key_filled_in_after_the_number_reds(tmp_path, monkeypatch):
    """The other key-presence false negative: the KEY is present from the start."""
    bundle = _dev_repo(tmp_path, monkeypatch, steps_first=False, null_first=True)
    failures = check.check_steps_block_is_pre_scored(bundle)
    assert failures and "were added" in failures[0], failures


def test_the_RETIRED_declaration_field_is_reported_as_retired(tmp_path):
    """`steps_pinned_after_scored_log` is dead weight now, and the message says so.

    Round 5 required the field; this branch then refused it on the false premise
    that it contradicts `written_before_any_scored_run` (it does not — that field
    describes the record's ORIGINAL freeze, so naming the log the pin postdates is
    the honest pairing). The message must name the retirement, not accuse the
    record of an incompatibility that does not exist.
    """
    record = {"schema": check.PREREG_SCHEMA,
              "written_before_any_scored_run": True,
              "written_before_any_scored_run_basis": f"no SCORED run; {check.UNSCORED}",
              "steps": {"conditional": [{"step": "S2.3"}]},
              check.STEPS_PINNED_AFTER: "capstone-measured-e0fc940b.log"}
    (tmp_path / check.MACHINE_RECORD).write_text(json.dumps(record))
    (tmp_path / "capstone-measured-e0fc940b.log").write_text("# result: COMPLETED\n")
    failures = check.check_steps_block_is_pre_scored(tmp_path)
    assert failures and "RETIRED" in failures[0], failures
    assert "Delete the field" in failures[0], failures


def test_no_steps_block_means_no_deviation(tmp_path):
    record = {"schema": check.PREREG_SCHEMA, "written_before_any_scored_run": True}
    (tmp_path / check.MACHINE_RECORD).write_text(json.dumps(record))
    (tmp_path / "capstone-measured-e0fc940b.log").write_text("x\n")
    assert check.check_steps_block_is_pre_scored(tmp_path) == []


def test_the_deviation_section_is_detected_from_a_REAL_ENTRY_not_a_mention():
    """This file MENTIONS `## Deviations` inside §8's prose, so a substring search
    would pass on the rule text alone; and a heading with no entry passes nothing."""
    assert check._document_has_deviations(PREREG.parent) is True

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / check.PREREG_NAME).write_text(
            "## 8. Change control\n\nrecorded in a `## Deviations` section appended below\n")
        assert check._document_has_deviations(d) is False, "a mention is not the section"
        (d / check.PREREG_NAME).write_text("## Deviations\n\n- nothing yet\n")
        assert check._document_has_deviations(d) is False, "a bare heading discloses nothing"
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1 — 2026-11-01\n\n- **Original rule**: x\n")
        assert check._document_has_deviations(d) is True
        # ⛔ the MERGED shape (#10215 \u00d7 #10525): the section is NUMBERED and its entries are
        # `### N.M`, because another numbered section of this document owns `## 10.` — reading only
        # the bare `## Deviations` spelling reported the SHIPPED document as carrying no deviation
        # at all, i.e. a \u00a78 gate failing on a heading's SHAPE rather than on the disclosure.
        (d / check.PREREG_NAME).write_text(
            "## 10. Deviations — the three amendments\n\n"
            "### 10.4 E4 — the ground was stated NOWHERE\n\n- **Original rule**: x\n")
        assert check._document_has_deviations(d) is True
        (d / check.PREREG_NAME).write_text(
            "## 10. Deviations — the three amendments\n\n### 10.4 E4\n\n## 11. Next\n\nprose\n")
        assert check._document_has_deviations(d) is False, (
            "a numbered entry is bounded at its own body, like the `### D…` spelling")
        # ⛔ round 7: the body scan ran to EOF, so a bare `### D1` above UNRELATED
        # prose passed. Both shapes below returned True before the fix.
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1\n\n## 9. Next\n\nunrelated prose\n")
        assert check._document_has_deviations(d) is False, (
            "prose in a LATER section is not this entry's body")
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1\n\n# Pre-registration\n\nlots of unrelated prose\n")
        assert check._document_has_deviations(d) is False
        # ⛔ round 8: stopping at ANY `#`-line made a properly structured entry read as
        # bodiless, so the repair the failure message prescribes could not clear it.
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1 — 2026-11-01\n\n#### Original rule\n\n"
            "all eight observed.\n\n#### New rule\n\nseven required.\n")
        assert check._document_has_deviations(d) is True, (
            "a `####`-structured entry carries its body one heading level down")
        # ⛔ and only the FIRST `### D` was examined: an empty D1 above a filled D2 is
        # a real disclosure, and reading one entry reported it as absent.
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1\n\n### D2 — 2026-11-01\n\n- **Original rule**: x\n")
        assert check._document_has_deviations(d) is True, (
            "every entry is examined, not only the first")
        # a `##` section that follows still ends the entry
        (d / check.PREREG_NAME).write_text(
            "## Deviations\n\n### D1\n\n#### Original rule\n\n## 9. Next\n\nprose\n")
        assert check._document_has_deviations(d) is False, (
            "the entry ends at its own level or shallower, not at its own subsections")
        # ⛔ round 9: an EMPTY Deviations section passed whenever a LATER section had a
        # `### D…`-prefixed heading — the section was never bounded, so a `### Data
        # sources` under `## Appendix` donated its body. Measured True before the fix.
        for later in ("## 9. Provenance\n\n### Denominator floors\n\n| n | src |\n",
                      "## Appendix\n\n### Data sources\n\ncorpus at x.\n",
                      "## Appendix\n\n```markdown\n### D1\n\nexample\n```\n"):
            (d / check.PREREG_NAME).write_text("## Deviations\n\n" + later)
            assert check._document_has_deviations(d) is False, (
                f"a heading in a LATER section is not this section's entry: {later!r}")


def test_the_shipped_record_carries_no_retired_key():
    """A SHALLOW-checkout claim only, stated as such.

    On this box (`git rev-parse --is-shallow-repository` → true) the rule abstains at
    `history_unavailable()` before the order arm runs, so all this test can prove is
    that the shipped record has not re-gained the RETIRED key. It CANNOT detect a
    post-scored pin here; the order arm is covered by the `_dev_repo` fixtures above,
    which build their own non-shallow repository (#10215 round 8).
    """
    assert check.check_steps_block_is_pre_scored(PREREG.parent) == []
    # ⛔ Read the FILE (#10215 round 9): `_record()` is a fixture BUILDER — it parses
    # the document's pins into a fixed literal dict and never opens the machine record,
    # so asserting against it was green whatever `preregistration.json` contained.
    shipped = json.loads((PREREG.parent / check.MACHINE_RECORD).read_text())
    assert check.STEPS_PINNED_AFTER not in shipped, (
        "the shipped record re-gained the retired key")


# ── #10509 / #10468: the step rule is scoped to the bundles that HAVE a step set ────────────
#
# `#10468` put a SECOND pre-registered bundle on the tool's `--dir` (`#10463`'s two-arm S3
# ablation). That bundle is a different epic with no eight-step concept, so an unscoped
# `check_step_partition` refused it on every run for not declaring a step set it never had —
# while `--dir` repeatability is exactly what makes one invocation read both.

#: The other pre-registered bundle on the CI invocation's `--dir` list (#10468).
NO_STEPS_BUNDLE = REPO_ROOT / "docs" / "evidence" / "5087-hypothesis"


def test_the_second_bundle_really_declares_no_step_set():
    """The premise, asserted rather than assumed: this bundle has no `steps` block on either side.

    If it ever gains one the scope test below stops testing what it claims, so the premise is
    pinned here rather than left to a comment.
    """
    pins = check.parse_pins((NO_STEPS_BUNDLE / check.PREREG_NAME).read_text())
    assert "steps" not in pins, sorted(pins)
    record = json.loads((NO_STEPS_BUNDLE / check.MACHINE_RECORD).read_text())
    assert "steps" not in record, sorted(record)


def test_a_bundle_with_no_step_set_is_not_refused_by_the_partition_rule():
    """REGRESSION (#10509 rebase): the unscoped rule failed this bundle for declaring nothing.

    The message was `pins.steps is empty — the capstone's step gate reads this split…`. True of the
    capstone; false of a bundle whose epic has no such gate, and it is not the bundle's defect to
    repair by inventing an eight-step declaration. `#no-git`/`--no-corpus` isolate the rule: the
    ordering half of this bundle is a separate finding and is not what this test is about.
    """
    report = check.run(NO_STEPS_BUNDLE, use_git=False, use_corpus=False)
    assert not any("pins.steps" in f for f in report["failures"]), report["failures"]
    assert report["ok"], report["failures"]


def test_the_capstone_bundle_is_STILL_governed_even_with_no_step_set_declared(tmp_path):
    """The scope must not be a blanket waiver: the capstone bundle never escapes by dropping it.

    This is the `DEFAULT_PREREG_DIR` arm of `check_step_partition_applies`. A document that loses
    its block must still be refused — otherwise "we deleted the split" and "the split was never
    ours" would read identically, which is the widening the rule exists to prevent.
    """
    assert check.check_step_partition_applies(PREREG_DIR, {}, {}) is True
    assert check.check_step_partition_applies(NO_STEPS_BUNDLE, {}, {}) is False


def test_the_CI_multi_bundle_invocation_is_green_on_the_second_bundle(capsys):
    """End to end, the shape CI runs: `--dir` twice, and the second bundle must not red the run.

    `--no-git`/`--no-corpus` again isolate the rule: the corpus half imports `tortoise`, and the
    ordering half is this bundle's separate finding (#10509 does not touch it).
    """
    code = check.main(["--dir", str(PREREG_DIR), "--dir", str(NO_STEPS_BUNDLE),
                       "--no-git", "--no-corpus", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0, payload
    assert payload["ok"] is True
    assert [r["ok"] for r in payload["reports"]] == [True, True], payload["reports"]


# ── #10509 review round: the step rule's own contract, made true ─────────────────────────────


def test_a_unicode_digit_issue_number_is_a_mismatch_not_a_traceback():
    """REGRESSION (#10509 review P2): `isdigit()` is True for characters `int()` REJECTS.

    `_issue_number` tested `str.isdigit()` and then called `int()`, so an `evidence_issue` holding
    `²` raised an uncaught `ValueError` — past `run()` (catches nothing) to `main()` (catches only
    `CheckError`) — escaping as a traceback with exit 1, the code this tool reserves for FINDINGS.
    The conversion must be TOTAL: a non-numeric value becomes a plain comparison mismatch.
    """
    assert "²".isdigit() and not int_succeeds("²")
    assert check._issue_number("²") == "²"
    assert check._issue_number("#10215") == 10215
    assert check._issue_number(10215) == 10215


def int_succeeds(text: str) -> bool:
    try:
        int(text)
    except ValueError:
        return False
    return True


def test_a_unicode_digit_in_a_real_record_is_a_finding_not_an_exit_1_traceback():
    """End to end: the escape above never reaches the CLI as a traceback."""
    rec = _record_with_steps()
    rec["steps"]["conditional"][0]["evidence_issue"] = "10²15"
    # no raise, and the value simply does not match the document's `10215`
    failures = check.check_step_partition(check.parse_pins(PREREG.read_text()), rec)
    assert any("evidence_issue" in f for f in failures), failures


def test_a_wrong_typed_pins_step_field_is_a_PARSE_error_on_EVERY_sub_key():
    """The docstring's contract, on the half that used to contradict it (#10509 review P2).

    `pins.steps = 5` was caught and downgraded to a finding (exit 1) while
    `pins.steps.unconditional = 5` raised (exit 2) — the SAME malformation, two different answers
    depending on which sub-key it landed in. All three are parse errors now.
    """
    for pins in ({"steps": 5}, {"steps": {"unconditional": 5}},
                 {"steps": {"conditional": 5}},
                 {"steps": {"unconditional": ["S0"], "conditional": 5}}):
        with pytest.raises(check.CheckError) as excinfo:
            check.check_step_partition(pins, {})
        assert "steps" in str(excinfo.value), (pins, excinfo.value)


def test_an_EMPTY_pins_step_block_stays_a_FINDING_not_a_parse_error():
    """The deliberate asymmetry: an absent block is a document defect, not unparseable input.

    Keeping this at exit 1 is what lets the capstone bundle's "we deleted the split" shape be
    reported as a weakened gate rather than as "the tool could not read its subject".
    """
    failures = check.check_step_partition({"steps": {}}, {})
    assert any("pins.steps is empty" in f for f in failures), failures


def test_a_malformed_record_conditional_entry_is_REPORTED_not_skipped():
    """REGRESSION (#10509 review P2): the record arm was weaker than the document arm.

    The pins loop reports `{step: …}`-less entries; the record loop silently dropped them. So a
    record that KEPT its valid entry and added a junk one compared clean, while the same document
    written one file over was refused.
    """
    rec = _record_with_steps()
    rec["steps"]["conditional"] = [*rec["steps"]["conditional"], {"condition": "no step key"}]
    failures = check.check_step_partition(check.parse_pins(PREREG.read_text()), rec)
    assert any("steps.conditional carries an entry that is not a" in f for f in failures), failures
