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
import os
import subprocess
import sys
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
    "python3 tools/capstone_preregistration_check.py "
    "--dir docs/evidence/7949-capstone --no-corpus"
)

#: The only `if:` forms that do not drop enforcement.
_ALWAYS_RUN = ("always()", "${{ always() }}")
#: The only step keys that can neither skip the step nor mask its exit code. An ALLOW-list, not an
#: enumeration of the silencing keys: `if:`/`continue-on-error:` skip a real failure, `shell:`
#: swallows its exit code, `working-directory:` can point at a stub tree, and `env:` (a PATH shim)
#: shadows `python3` so the real check never runs. Enumerating them one at a time is what left the
#: next one open (`tests/test_merge_throughput.py` records that measurement for its own gate step).
_SAFE_STEP_KEYS = {"name", "run", "id", "timeout-minutes"}


def _code(step: dict) -> str:
    """The step's `run:` with comment lines removed — a `#`-prefixed mention is not an invocation."""
    return "\n".join(
        line for line in str(step.get("run") or "").splitlines()
        if not line.strip().startswith("#")
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
    exact expected command; that step carries no key that can skip it or mask its exit code; no
    scope above it shims `python3` or overrides the shell; the owning job checks out at
    `fetch-depth: 0` (the ordering half reads commit epochs) and feeds the required aggregate.

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

    run = _code(step).replace("\\\n", " ").strip()
    if run != ORDERING_HALF_INVOCATION:
        violations.append(
            f"the gate step must invoke EXACTLY `{ORDERING_HALF_INVOCATION}` — no shell operator "
            f"and no trailing anything, or a real failure reports green; got {run!r}"
        )
    extra_keys = sorted(str(k) for k in set(step) - _SAFE_STEP_KEYS)
    if extra_keys:
        violations.append(
            f"the gate step may carry only {sorted(_SAFE_STEP_KEYS)}; found {extra_keys} — every "
            "other Actions step key can skip the step or mask its exit code"
        )
    if step.get("if", "always()") not in _ALWAYS_RUN:
        violations.append(
            f"the gate step must be unconditional; got if: {step.get('if')!r}"
        )

    for scope, label in ((workflow, "python-ci.yml"), (job, f"the `{job_name}` job")):
        for key in ("shell", "working-directory"):
            value = ((scope.get("defaults") or {}).get("run") or {}).get(key)
            if value:
                violations.append(
                    f"`defaults.run.{key}` on {label} ({value!r}) can swallow the gate's exit "
                    "code or run it against a stub tree"
                )
        if (scope.get("env") or {}).get("PATH"):
            violations.append(
                f"a `PATH` entry in `env:` on {label} can resolve `python3` to a stub that "
                "exits 0, leaving the step's own shape clean"
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

    gate_job = jobs.get("python-ci-gate") or {}
    if job_name not in (gate_job.get("needs") or []):
        violations.append(
            f"the `{job_name}` job must be in `python-ci-gate.needs`, or the gate can red without "
            "blocking the merge"
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
}


def _mutations():
    """(label, mutate) pairs — each leaves the command in place and silences it."""

    def gate_step(workflow):
        return next(
            s for s in workflow["jobs"]["changes"]["steps"]
            if "tools/capstone_preregistration_check.py" in (s.get("run") or "")
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
        "a job-level `continue-on-error: true`": lambda w: w["jobs"]["changes"].__setitem__(
            "continue-on-error", True),
        "a job-level `container:`": lambda w: w["jobs"]["changes"].__setitem__(
            "container", "stub:latest"),
        "a job-level `if:`": lambda w: w["jobs"]["changes"].__setitem__("if", "false"),
        "a second decoy gate step": lambda w: w["jobs"]["changes"]["steps"].append(
            {"name": "decoy", "run": ORDERING_HALF_INVOCATION}),
        "a sibling `$GITHUB_PATH` writer": lambda w: w["jobs"]["changes"]["steps"].insert(
            0, {"name": "shim", "run": "echo /tmp/shim >> $GITHUB_PATH"}),
        "a sibling `$GITHUB_ENV` writer": lambda w: w["jobs"]["changes"]["steps"].append(
            {"name": "shim", "run": "echo PATH=/tmp/shim >> $GITHUB_ENV"}),
        "dropping it from the required aggregate": lambda w: w["jobs"][
            "python-ci-gate"]["needs"].remove("changes"),
    }


@pytest.mark.parametrize("label", list(_mutations()))
def test_the_ci_pin_rejects_every_silencing_idiom(label):
    """The failing direction for the pin above: each mutation is invisible to the PRE-FIX pin.

    Every case keeps the gate's command string present, so the old membership assertion stayed
    green while the check could never fail a PR; the strict pin must name the violation.

    Every branch of the pin that can leave the gate GREEN has a case here. The one branch without
    one — "the job must check out the repo" — is the opposite shape: deleting the `changes`
    checkout reds the job instead of greening the gate, and the PRE-FIX pin already refused it,
    the same way it already refused a shallow checkout
    (`test_the_ci_pin_still_catches_a_shallow_checkout`), so claiming it here as a pre-fix hole
    would overstate the gap.
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
