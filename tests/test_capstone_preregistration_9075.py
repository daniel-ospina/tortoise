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
    assert failures and "exit=3" in failures[0], failures


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
        "written_before_any_run": True,
        "written_before_any_run_basis": f"no scored run exists; the {check.UNSCORED} log predates it",
        "unscored_runs_before_this_registration": sorted(
            pins["pre_existing_unscored_logs"]),
        "corpus": {"fixtures_hash": pins["corpus"]["fixtures_hash"]},
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


def test_a_machine_record_without_the_run_order_assumption_fails(tmp_path):
    rec = _record(written_before_any_run=False)
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("written_before_any_run" in f for f in failures), failures


def test_the_run_order_claim_must_state_its_basis(tmp_path):
    """An unqualified `true` is the claim #9075 exists to refuse; the basis names the unscored log."""
    rec = _record(written_before_any_run_basis="because we say so")
    d = _materialise(tmp_path, rec)
    failures = check.check_machine_record(d, check.parse_pins(PREREG.read_text()))
    assert any("without naming the basis" in f for f in failures), failures


# ── the CLI contract ────────────────────────────────────────────────────────


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
