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
import json
import os
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
    # ⛔ …but it must not become an assertion this repo cannot satisfy everywhere (review cycle 3,
    # finding 2). In a depth-1 checkout there is no `origin/main` and no local `main`, so the honest
    # report is the unresolved marker — asserting a RESOLVED base there would red a correctly ordered
    # tree from inside the test that exists to prevent exactly that. A base is required only where
    # one is measurable.
    named = {"origin/main", "main", "HEAD"}
    if check.history_unavailable():
        named.add(check.UNRESOLVED_BASE)
    assert report["ordering_base"] in named, report["ordering_base"]
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


def _write_and_commit(repo: Path, name: str, epoch: int) -> Path:
    target = repo / "docs" / "evidence" / "7949-capstone" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x\n")
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
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1
    assert "not strictly after" in failures[0], failures


def test_ordering_is_not_satisfied_by_the_same_second(tmp_path, monkeypatch):
    """'Same second' is not 'before' — the two files landing in one commit is not an ordering."""
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
    pins = check.parse_pins(PREREG.read_text())
    assert check.check_machine_record(PREREG_DIR, pins) == []


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

    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "MODIFIED at" in failures[0], failures


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
    """
    repo = _repo(tmp_path, monkeypatch)
    log = _write_log(repo, "e1-e2.log", epoch=1_700_000_000)
    # A side branch edits the log, then main merges it with --no-ff.
    _git(repo, "checkout", "-q", "-b", "side")
    log.write_text(UNSCORED_LOG.replace("after_share=0.6", "after_share=0.25"))
    _git(repo, "add", str(log.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", "side edit", epoch=1_700_000_050)
    _git(repo, "checkout", "-q", "-")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge side", "side", epoch=1_700_000_200)
    prereg = _write_and_commit(repo, check.PREREG_NAME, epoch=1_700_000_100)
    failures = check.check_ordering(prereg, [log], _exempt())
    assert failures and "MODIFIED at" in failures[0], failures


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
         # Re-cut 2026-10-30 (PR #10186): an `env:` key added — `CI_SHARD_ROUTING`,
         # the runner-split opt-in's ONE lever — with the `run:` script byte-identical.
         # The lever cannot live at job scope (any job-scope key sits above the gate step
         # and hooks its interpreter, which is what `_SAFE_JOB_ENV` refuses), so it is
         # carried by each of this job's TWO emitters; the digests move by the env block
         # alone, deliberately, per this pin's own instruction.
         "f0e928484d97c448337a2110b86dc54718b6fd5e36611369ae515aafff88c3f0"),
        ("Non-gating lanes — what this gate does NOT run (#10194)",
         "9b6ec88d732ebef44f83fa29380ed9f73d8d9daa01cc61964939db0a1bd26bc7"),
        ("Tier-2 duration split (#1473)",
         # Re-cut 2026-10-30 (PR #10186), same env-only reason as the push emitter above:
         # this emitter's output OVERRIDES the push matrix on tier-2 PRs, so a lever on
         # the push step alone is dead on exactly the event every PR uses.
         "6a2cee39ab47ddba253e5c4a46b6a01650ea4c9a58cad56f68ba9255706bb7ca"),
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
    failures = check.check_ordering(prereg, [log])
    assert len(failures) == 1, failures
    assert "not strictly after" in failures[0], failures
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
    base — the depth-1 shape the sibling test below commits as LEGITIMATE — and the tool prints
    `(unresolved)` there, so a second resolve reds a correctly-ordered tree.
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

    That is what a squash-merged `main` looks like to a branch that has not been rebased, and it is
    the only shape in which the base reading and the PR-internal reading disagree on a REFUSAL.
    """
    branch = _git_out(repo, "symbolic-ref", "--short", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "squashed-base")
    _git(repo, "rm", "-rq", "--cached", "docs")
    _commit_together(repo, epoch=epoch, names=names)
    sha = _git_out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-qf", branch)
    return sha


def test_a_depth_one_checkout_without_a_base_reports_the_unresolved_base(tmp_path, monkeypatch):
    """REGRESSION (#10463 review cycle 3, finding 2): the reported base must tolerate depth 1.

    The acceptance test asserts `report["ordering_base"]` is one of the resolved candidates. In a
    depth-1 checkout of a single branch there is no `origin/main` and no local `main`, so the honest
    report is `(unresolved)` — and an assertion that insists on a resolved ref reds a correctly
    ordered tree from inside the test written to prevent exactly that. The value must stay a
    reported fact; what it must NOT do is turn the unprovable third outcome into a failure.
    """
    origin = _repo(tmp_path, monkeypatch)
    bundle = origin / "docs" / "evidence" / "7949-capstone"
    bundle.mkdir(parents=True, exist_ok=True)
    for item in PREREG_DIR.iterdir():
        if item.is_file():
            (bundle / item.name).write_bytes(item.read_bytes())
    _git(origin, "add", "docs")
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
    assert report["ordering_base"] == check.UNRESOLVED_BASE, report["ordering_base"]
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


def test_a_squash_collapsed_base_refuses_even_when_the_contribution_is_ordered(tmp_path, monkeypatch):
    """THE LOG-SIDE HALF of the squash-invariance rule (#10463 review cycle 7, finding 1).

    WHY THIS IS THE SHAPE. The rule reads each log's add-commit **on the base** when the log is
    already merged there, and in the contribution when it is not. That log-side read had NO witness:
    forcing `base_log_epochs = []` (so every log is read from the contribution) left all 212 tests
    green, and on the real #10468 shape it flipped a REFUSAL into a silent PASS. The registration
    side was covered; the log side — half the predicate — was not.

    The shape that discriminates: the base carries the registration and the log in ONE squash commit
    (equal epochs), while THIS contribution carries them in order. Reading the log from the
    contribution alone therefore ACCEPTS it; reading it from the base REFUSES it. Only a test built
    on `_squashed_base` can tell those two implementations apart, which is what its docstring claims
    it is for.
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
