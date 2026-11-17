"""`tools/record_runner.py` — the runner for the record fold (`#7879` hole 1).

WHAT THIS SUITE DOES AND DOES NOT PROVE
=======================================
The fold itself (`record_projection.replay`) is `tests/test_record_projection.py`'s
subject, and its DB-level behaviour is the PGlite / `test-record-postgres` lanes'.
This file is the RUNNER: which records it hands the fold, where it resumes, and —
the property that matters most — what it does when it must not write.

So the database is STUBBED here, deliberately and narrowly:

* :func:`open_record` is replaced, so no DSN is needed and no connection is
  opened (the suite runs everywhere, including the DB-free matrix);
* :func:`read_checkpoint` is replaced with a value the test states, because the
  checkpoint's SQL is `journal_event`'s and belongs to the record's own suite;
* :func:`replay` is replaced where the test is about the runner's GATE (the
  acknowledgement, the cut-over refusal, the no-op) and NOT where the test is
  about the runner's READ (journal parsing, batch slicing, resume arithmetic).

⛔ What that cannot catch, stated so a green never over-claims: a checkpoint whose
SQL is wrong, a `journal_event` write that does not land, or a replay that is not
idempotent. There is NO live leg in this file, deliberately: those three need a
real Postgres, which the `test-record-postgres` lane and the PR body's evidence
carry. This suite proves the runner's DECISIONS over a stubbed record; it cannot
prove the record.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import record_runner  # noqa: E402
from record_runner import (  # noqa: E402
    EXIT_OK,
    EXIT_REFUSED,
    EXIT_USAGE,
    Checkpoint,
    Journal,
    RunnerError,
    batch_slices,
    build_plan,
    main,
    read_journal,
)

FIXTURE = REPO_ROOT / "supabase" / "tests" / "fixtures" / "record_projection_journal.jsonl"


# ── journal reading ──────────────────────────────────────────────────────────


def test_the_fixture_journal_is_read_by_the_journals_own_reader():
    """A real journal, read strictly, in file order — the shape `replay` wants."""
    journal = read_journal(FIXTURE)
    assert isinstance(journal, Journal)
    assert len(journal) == 39
    assert journal.segmented is False
    # The reader, not this tool, decides what is a record: every entry is a
    # mapping with a `type`, which is what `mutations_for` dispatches on.
    assert all(isinstance(r.get("type"), str) for r in journal.records)
    assert journal.records[0]["type"] == "IngestStarted"


def test_a_missing_journal_is_a_refusal_and_not_an_empty_one(tmp_path):
    """A mistyped path must not read as "nothing to migrate" (the vacuity class).

    ⛔ Two shapes, because they reach the refusal by different routes: with the
    parent present the sentence is reached anyway, so a test using only that
    shape cannot see the layout probe raising FIRST (a mistyped path whose
    parent is also gone escaped as `FileNotFoundError` from `iterdir()`).
    """
    for given in (tmp_path / "nope.jsonl", tmp_path / "gone" / "events.jsonl"):
        with pytest.raises(RunnerError) as exc:
            read_journal(given)
        assert "does not exist" in str(exc.value)

    # ⛔ The THIRD shape: the path EXISTS and is a directory, but holds no
    # journal at all (the classic mistype: a graph dir's PARENT). Reading it as
    # zero records made `apply` print "NOTHING TO DO — every journal position is
    # already in the record", i.e. the vacuity this test is named against.
    empty = tmp_path / "not-a-graph"
    empty.mkdir()
    (empty / "notes.txt").write_text("unrelated", encoding="utf-8")
    with pytest.raises(RunnerError) as exc:
        read_journal(empty)
    assert "no journal in it" in str(exc.value)
    # …and a REAL graph dir with an empty but present journal is still readable:
    real = tmp_path / "graph"
    real.mkdir()
    (real / "events.jsonl").write_text("", encoding="utf-8")
    assert len(read_journal(real)) == 0


def test_strict_reading_refuses_mid_file_damage_and_salvage_counts_it(tmp_path):
    """`salvage` is an explicit choice, and the report says when it was taken.

    A fold that SKIPPED a line would produce a record missing a mutation and
    report success — so the strict read must raise, and the salvaging read must
    carry its own damage count into the report rather than looking clean.
    """
    journal = tmp_path / "events.jsonl"
    good = json.dumps({"type": "IngestStarted", "ts": "2026-01-01T00:00:00Z"})
    journal.write_text(good + "\n{not json\n" + good + "\n", encoding="utf-8")

    # The reader's own class, not a blind `Exception`: a test that accepts any
    # error also accepts a typo in this test.
    with pytest.raises(ValueError):
        read_journal(journal)

    salvaged = read_journal(journal, salvage=True)
    assert salvaged.salvage is True
    assert salvaged.corrupt == 1
    assert len(salvaged) == 2


def test_a_directory_journal_is_read_as_a_layout_not_as_one_file(tmp_path):
    """A graph directory is a LAYOUT: reading one segment would under-fold.

    `record_allocator_cli._journal_floor` REFUSES this shape for its own purpose
    (it counts one file). The runner instead reads it correctly, through
    `journal_segments.read_all_segments` — the reader that owns the layout.
    """
    from tortoise import journal_segments

    active = tmp_path / journal_segments.ACTIVE_NAME
    active.write_text(json.dumps({"type": "IngestStarted"}) + "\n", encoding="utf-8")

    journal = read_journal(tmp_path)
    assert journal.segmented is True
    assert len(journal) == 1


def test_a_sealed_layout_with_no_manifest_is_REFUSED_not_half_read(tmp_path):
    """⛔ The one direction this must never fail in: a silent partial read.

    A sealed sibling whose manifest is missing (`seal_active` renames the active
    file, then writes the manifest — a process killed between the two leaves
    exactly this) has no established order or completeness. Folding one segment
    of it would produce a record that is plausible, short, and reports success.
    The journal's own reader refuses; the runner must not catch it.
    """
    from tortoise import journal_segments

    (tmp_path / "events.0001.jsonl").write_text(
        json.dumps({"type": "IngestStarted"}) + "\n", encoding="utf-8")
    (tmp_path / journal_segments.ACTIVE_NAME).write_text(
        json.dumps({"type": "IngestStarted"}) + "\n", encoding="utf-8")

    from tortoise.journal_segments import SegmentSequenceError

    with pytest.raises(SegmentSequenceError) as exc:
        read_journal(tmp_path)
    assert "manifest" in str(exc.value)


# ── batching, checkpoints, resume arithmetic ─────────────────────────────────


def test_batches_are_bounded_and_contiguous_and_start_at_the_resume_point():
    slices = batch_slices(105, start_at=20, batch_size=40)
    assert slices == [(20, 40), (60, 40), (100, 5)]
    # Every index the slices cover, exactly once, in order — the property that
    # makes a killed run resumable instead of skipping or repeating a record.
    covered = [off + i for off, n in slices for i in range(n)]
    assert covered == list(range(20, 105))


def test_the_resume_point_is_the_sequence_position_not_the_list_index():
    """`start_at` is an index; the fold is told the POSITION, and they must agree."""
    report = build_plan(journal=Journal(path=Path("x"), records=[
        {"type": "IngestStarted"} for _ in range(5)]),
        org_id="o", graph_id="g", start_at=2, batch_size=2, limit=None,
        allocate=False)
    assert [b["start_seq"] for b in report.batches] == [3, 5]
    assert report.events_to_consume == 3


def test_a_checkpoint_gap_resumes_at_the_gap_not_after_the_high_water_mark():
    """A missing position below `max` is a disagreement, and it names itself."""
    complete = Checkpoint(high_seq=10, present=10, first_gap=None)
    assert complete.next_seq == 11 and complete.contiguous is True

    gapped = Checkpoint(high_seq=10, present=9, first_gap=4)
    assert gapped.next_seq == 4 and gapped.contiguous is False


def test_limit_bounds_the_run_without_changing_where_it_starts():
    """`--limit` takes the FIRST N from the resume point — it never re-windows it.

    Two batches of 50 and 10 is exactly 60 records from index 10; a plan that
    instead made the last batch fill to 20 would fold 70 and silently exceed the
    bound the operator set.
    """
    assert batch_slices(200, start_at=10, batch_size=50, limit=60) == [(10, 50), (60, 10)]


def test_a_batch_size_or_limit_below_one_is_a_refusal_not_a_silent_full_run():
    """The guard ITSELF refuses a below-1 bound, at the level that computes windows.

    ⛔ This is the unit arm only — it calls `batch_slices` directly and therefore
    cannot see a verb that never reaches it. `apply` took its `consumable == 0`
    no-op arm BEFORE the slicer, so `apply --limit 0` printed "every journal
    position is already in the record" over an untouched record and exited 0
    (review P1). The CLI arm — and the property this test's name describes — is
    pinned by `test_apply_refuses_an_impossible_bound_...` below; this row stays
    as the guard's own regression pin, because its mutant HANGS (`take = 0`).
    """
    for kwargs in ({"batch_size": 0}, {"batch_size": 5, "limit": 0}):
        with pytest.raises(RunnerError):
            batch_slices(10, **kwargs)


def test_a_record_longer_than_the_journal_is_refused():
    """Two stores holding different histories must not be folded into each other."""
    with pytest.raises(RunnerError) as exc:
        batch_slices(5, start_at=9, batch_size=5)
    assert "not the same journal" in str(exc.value)


# ── the plan writes nothing (structurally) ───────────────────────────────────


def test_plan_opens_NO_connection_at_all_unless_asked_for_the_checkpoint(
        monkeypatch, capsys):
    """The dry run's "zero rows" is structural: the writing path is unreachable.

    A test that asserted "0 rows landed" would need a database and would pass if
    a bug merely wrote somewhere else. This asserts the stronger property — the
    planner never opens a record connection — so `--dry-run` cannot write.
    """
    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("plan opened a record connection")
    monkeypatch.setattr(record_runner, "open_record", boom)

    assert main(["plan", "--org-id", "o", "--graph-id", "g",
                 "--journal", str(FIXTURE), "--batch-size", "20"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "rows_written: 0" in out
    assert "statements_executed: 0" in out
    # ⛔ AND IT DOES NOT HAND OVER A NUMBER `apply` WOULD REJECT: this plan never
    # read the record, so on a non-empty record `apply` computes a different one.
    assert "--acknowledge-plan" not in out
    assert "plan --from-checkpoint" in out


def test_plan_json_reports_zero_writes_and_the_event_census(capsys):
    assert main(["plan", "--org-id", "o", "--graph-id", "g",
                 "--journal", str(FIXTURE), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows_written"] == 0 and payload["statements_executed"] == 0
    assert payload["events_to_consume"] == 39
    assert payload["totals"]["events_by_type"]["PointAdded"] >= 1


def test_the_plan_states_its_ZEROS_from_the_runs_own_ledger_not_a_constant():
    """⛔ review P2: `rows_written: 0` was a LITERAL in both renderers.

    An expected value composed from a constant outside the code under test cannot
    be perturbed by any mutant of that code ("oracles that cannot fail",
    arXiv 2608.17214; constant-mutation rows in the mutation-testing literature).
    The four assertions built on those literals were therefore true under EVERY
    mutation of `tools/record_runner.py` — including one that made `plan` write
    to the record — while reading as evidence that the dry run's headline
    falsifier was covered.

    The fix is not a better assertion on the same literals: it is to make the
    number reachable. `PlanReport.executed` is the run's write ledger, `apply`
    fills the SAME type from the fold's report (pinned below), and both renderers
    read it. This test drives a NON-ZERO ledger through both surfaces, so the
    mutant "restore the literal" reddens here: on the pre-fix code the JSON says
    `0` and the text says `rows_written: 0` whatever the ledger holds.
    """
    report = record_runner.PlanReport(
        org_id="o", graph_id="g",
        journal={"path": "j", "records": 0, "segmented": False,
                 "salvage": False, "corrupt_lines": 0, "torn_tails": 0},
        checkpoint=None, cursor=None, start_at=0, events_to_consume=0,
        totals={"events": 0, "statements": 0, "position_rows": 0,
                "events_by_type": {}},
        executed=record_runner.WriteLedger(rows_written=7, statements_executed=11),
    )
    payload = report.as_dict()
    assert payload["rows_written"] == 7, payload
    assert payload["statements_executed"] == 11, payload
    text = record_runner._render_plan(report)
    assert "rows_written: 7" in text, text
    assert "statements_executed: 11" in text, text


def test_plan_finds_an_undeclared_record_type_BEFORE_anything_could_write(tmp_path,
                                                                        capsys):
    """`#5285`'s totality rule reaches the operator as a refusal, not a crash."""
    journal = tmp_path / "events.jsonl"
    journal.write_text(json.dumps({"type": "NoSuchRecordType"}) + "\n", encoding="utf-8")
    assert main(["plan", "--org-id", "o", "--graph-id", "g",
                 "--journal", str(journal)]) == EXIT_REFUSED
    assert "NoSuchRecordType" in capsys.readouterr().err


# ── the gates on a run that WOULD write ──────────────────────────────────────


class _StubCursor:
    """Accepts the runner's own `set_config` and nothing else.

    `apply_batches` sets a transaction-local `statement_timeout` before handing
    the batch to the fold, so a stub with no cursor would fail on the runner's
    OWN statement and the test would be measuring the stub. `replay` itself is
    patched in these legs, so any SQL beyond that one call is a bug here.
    """

    def __init__(self, owner):
        self._owner = owner

    def execute(self, sql, params=None):
        self._owner.statements.append((sql, params))

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _StubConn:
    """Enough of a connection for the runner's own decisions. Runs no fold SQL."""

    def __init__(self):
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0
        self.statements = []

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed += 1

    def cursor(self):
        return _StubCursor(self)


def _patched(monkeypatch, *, checkpoint: Checkpoint, cursor=None):
    conn = _StubConn()
    monkeypatch.setattr(record_runner, "open_record", lambda *a, **k: conn)
    monkeypatch.setattr(record_runner, "read_checkpoint", lambda *a, **k: checkpoint)
    monkeypatch.setattr(record_runner, "read_cursor_state", lambda *a, **k: cursor)
    return conn


def test_apply_refuses_to_write_without_the_plans_number(monkeypatch, capsys):
    """#11469's report-before-drop guarantee, made mechanical.

    An operator cannot acknowledge a number they have not been shown, so a run
    that would write is refused until `plan` has produced one.
    """
    _patched(monkeypatch, checkpoint=Checkpoint())
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_REFUSED
    assert "--acknowledge-plan 39" in capsys.readouterr().err


def test_apply_refuses_a_stale_acknowledgement_naming_both_numbers(monkeypatch, capsys):
    """A journal that grew since the plan means the plan describes a different run.

    ⛔ This assertion names the GATE'S OWN sentence and proves the fold never
    ran. Asserting the digits alone is not discriminating: `"7" in err` is
    satisfied by `#7879` and `"39"` by `seq 1..39` in an UNRELATED downstream
    refusal, so this test stayed GREEN with the gate deleted (measured: the
    mismatch branch short-circuited to a no-op → 24 passed).
    """
    conn = _patched(monkeypatch, checkpoint=Checkpoint())

    def boom(*a, **k):  # pragma: no cover - the gate must refuse before this
        raise AssertionError("the fold ran with a rejected acknowledgement")
    monkeypatch.setattr(_replay_attr(), "replay", boom)

    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "7"])
    assert rc == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "--acknowledge-plan 7 does not match this run" in err
    assert "39 event(s) are unconsumed from seq 1" in err
    # Refused BEFORE anything was written — not after a rolled-back attempt. The
    # stub records the runner's own `statement_timeout` too, so any post-gate SQL
    # would show up here.
    assert conn.committed == 0
    assert conn.statements == []


def test_a_fully_consumed_journal_is_a_no_op_that_says_so(monkeypatch, capsys):
    """The idempotency falsifier, at the level this suite can execute.

    A second run must not "replay and rely on ON CONFLICT" — it must decline to
    replay at all, and say so with counts an operator can quote.
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=39, present=39))
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "NOTHING TO DO" in out and "consumed=0" in out


def test_apply_refuses_an_impossible_bound_and_does_not_call_it_a_completion(
        monkeypatch, capsys):
    """Review P1 (#13210): `--limit 0` is an ARGUMENT error, not "nothing to do".

    ⛔ THE TWO FACTS THE DEFECT MERGED ARE SEPARATE, and this test is the one that
    separates them. Pre-fix, `apply --limit 0` over an EMPTY record printed

        NOTHING TO DO — every journal position is already in the record.
        checkpoint after: present 0, high 0, first gap None → next run at seq 1

    and exited 0 — a completion claim contradicted twice by its own report, for a
    journal of 39 records against a record holding none. `plan --limit 0` refused
    the same input, so the tool contradicted itself across verbs. A scripted
    backfill reads rc 0 plus that sentence as *complete*, which is why the false
    sentence AND the false exit code are both asserted here: the mutant that
    removes the guard reddens both (measured at the pre-fix commit).

    Both bounds are covered, because the refusal must be uniform: a below-1
    `--batch-size` has the same defect on the same early-return path.
    """
    for flag in (("--limit", "0"), ("--batch-size", "0")):
        conn = _patched(monkeypatch, checkpoint=Checkpoint())
        # The fold must never be reached: a refusal that folded first would be a
        # write behind a rejected invocation.
        monkeypatch.setattr(_replay_attr(), "replay", _must_not_run)
        with pytest.raises(SystemExit) as exc:
            main(["apply", "--org-id", "o", "--graph-id", "g",
                  "--journal", str(FIXTURE), flag[0], flag[1],
                  "--acknowledge-plan", "39"])
        captured = capsys.readouterr()
        # ⛔ rc 2 (USAGE), not 1 (REFUSED): a bad bound is a fact about THIS
        # INVOCATION, and `1` means "the record said no, do not re-run blindly".
        # A backfill chain that reads 1 halts on its own typo.
        assert exc.value.code == EXIT_USAGE, (flag, captured)
        assert f"{flag[0]} must be >= 1" in captured.err, captured.err
        # ⛔ The false sentence, asserted ABSENT — the whole defect was this line.
        assert "NOTHING TO DO" not in captured.out, captured.out
        assert "already in the record" not in captured.out, captured.out
        assert conn.committed == 0
        assert conn.statements == []


def test_an_impossible_bound_is_refused_BEFORE_any_record_is_read(monkeypatch, capsys):
    """The bound describes the INVOCATION, so it is checked before the record.

    A `--limit 0` run that opened a connection first would still be correct-ish
    for `apply`, but the property that makes the refusal trustworthy is stronger:
    nothing was read, so nothing can be claimed. This also covers the `plan` arm,
    which reached the same refusal only indirectly — through `build_plan` →
    `batch_slices` — so its sentence and its cost were a side effect of planning.

    ⛔ `read_journal` IS PATCHED TOO, and that is the assertion this test was
    missing (review P2): with only `open_record` and `replay` guarded, a guard
    moved to sit AFTER `read_journal(...)` was invisible to every test in the
    suite (measured: the reordered guard left 866 passed, 2 skipped). "Before any
    record is read" must mean the JOURNAL read as well as the connection.
    """
    def boom(*a, **k):  # pragma: no cover - must never run
        raise AssertionError("an impossible bound opened a record connection")
    monkeypatch.setattr(record_runner, "open_record", boom)
    monkeypatch.setattr(record_runner, "read_journal", boom)
    monkeypatch.setattr(_replay_attr(), "replay", _must_not_run)

    for argv in (
        ["apply", "--limit", "0", "--acknowledge-plan", "39"],
        ["apply", "--batch-size", "0", "--acknowledge-plan", "39"],
        ["plan", "--limit", "0"],
        ["plan", "--batch-size", "0"],
    ):
        with pytest.raises(SystemExit) as exc:
            main([argv[0], "--org-id", "o", "--graph-id", "g",
                  "--journal", str(FIXTURE), *argv[1:]])
        assert exc.value.code == EXIT_USAGE, argv
        assert "must be >= 1" in capsys.readouterr().err, argv


def test_a_LEGITIMATE_bound_still_runs_bounded(monkeypatch, capsys):
    """⛔ The other side of the guard: `>= 1` must not become `> 1` (review P2).

    Every bound test above is a below-1 case, so the guard could have been
    tightened to `<= 1` — refusing a perfectly legal `--limit 1` / `--batch-size 1`
    — and the whole suite stayed green (measured: 866 passed, 2 skipped). The
    legitimate boundary is a behaviour this tool must keep, so it is pinned.
    """
    conn = _patched(monkeypatch, checkpoint=Checkpoint())
    monkeypatch.setattr(_replay_attr(), "replay", _fold_report_for)
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--limit", "5", "--acknowledge-plan", "5"])
    assert rc == EXIT_OK, rc
    out = capsys.readouterr().out
    assert "consumed: 5 events" in out
    assert conn.committed == 1

    for flag in (("--limit", "1"), ("--batch-size", "1")):
        rc = main(["plan", "--org-id", "o", "--graph-id", "g",
                   "--journal", str(FIXTURE), flag[0], flag[1]])
        assert rc == EXIT_OK, (flag, rc)


def test_status_DISCLOSES_a_salvage_read_and_withdraws_the_completion_claim(
        monkeypatch, capsys):
    """⛔ P1 (PR #13210): `status` published 3 of the 6 journal keys, and asserted completion.

    `plan`/`apply` publish `salvage`/`corrupt_lines`/`torn_tails` and warn that a
    SKIPPED line SHIFTS every later position; `status` dropped all three and
    computed `fully_consumed` from `len(journal.records)`, so a **salvaged** read
    could report `fully_consumed: true` over a record that is missing the skipped
    record's mutation — the exact harm `read_journal`'s docstring promises the
    report will disclose. Measured before the fix on a 3-record journal with one
    corrupt line: `status` printed `events remaining: 0` / no damage line, and
    `fully_consumed: True`.
    """
    journal = Journal(path=FIXTURE,
                      records=[{"type": "IngestStarted"},
                               {"type": "PointAdded"},
                               {"type": "PointAdded"}],
                      segmented=False, salvage=True, corrupt=1, torn=0)
    monkeypatch.setattr(record_runner, "read_journal", lambda *a, **k: journal)
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=3, present=3))

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal"]["salvage"] is True
    assert payload["journal"]["corrupt_lines"] == 1
    assert payload["journal"]["torn_tails"] == 0
    # the field a chain gates on: unknown, never True, over a skipped line
    assert payload["fully_consumed"] is None
    assert payload["completeness_unknown"] is True

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "SALVAGE" in out
    assert "damaged input: 1 mid-file line(s)" in out
    assert "NOT a completeness claim" in out
    assert "complete: UNKNOWN" in out


def test_status_on_a_SEGMENTED_layout_cannot_claim_completion(monkeypatch, capsys):
    """The worse arm: a segmented reader ALWAYS salvages and returns no counters.

    `corrupt is None` means NOT REPORTED, so there is not even a number to print —
    which is why `fully_consumed` must be `None` here and the text must say the
    damage is unreportable rather than print a reassuring zero.
    """
    journal = Journal(path=FIXTURE,
                      records=[{"type": "IngestStarted"}, {"type": "PointAdded"}],
                      segmented=True, salvage=True, corrupt=None, torn=None)
    monkeypatch.setattr(record_runner, "read_journal", lambda *a, **k: journal)
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=2, present=2))

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal"]["corrupt_lines"] is None
    assert payload["journal"]["torn_tails"] is None
    assert payload["fully_consumed"] is None
    assert payload["completeness_unknown"] is True

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "damage NOT REPORTED" in out
    assert "complete: UNKNOWN" in out


def test_status_on_a_SEGMENTED_layout_WITHOUT_salvage_still_cannot_claim_completion(
        monkeypatch, capsys):
    """⛔ P2 (PR #13210 review): the `corrupt is None` half of the predicate.

    The test above sets `salvage=True`, so `journal.salvage` alone satisfies it —
    and `read_journal` returns `salvage=<THE CALLER'S FLAG>` for a segmented
    layout, never `True`. `status --journal <graph dir>` with no `--salvage` is
    therefore `segmented=True, salvage=False, corrupt=None`: damage unmeasurable,
    and a `salvage`-only predicate lets `remaining <= 0 and contiguous` claim
    `fully_consumed: true` over a read that cannot see a skipped line.

    MEASURED as the discrimination gap this pins: with
    `completeness_unknown = journal.salvage`, the whole suite stays GREEN
    (51 passed) — this row is what reds it.
    """
    journal = Journal(path=FIXTURE,
                      records=[{"type": "IngestStarted"}, {"type": "PointAdded"}],
                      segmented=True, salvage=False, corrupt=None, torn=None)
    monkeypatch.setattr(record_runner, "read_journal", lambda *a, **k: journal)
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=2, present=2))

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal"]["segmented"] is True
    assert payload["journal"]["salvage"] is False
    assert payload["events_remaining"] == 0, payload
    assert payload["completeness_unknown"] is True
    assert payload["damage_unreported"] is True
    # the field a chain gates on: never True over damage it could not see
    assert payload["fully_consumed"] is None

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "damage NOT REPORTED" in out
    assert "complete: UNKNOWN — this layout's reader reports no damage counters" in out


def test_a_salvage_read_that_skipped_NOTHING_can_still_claim_completion(monkeypatch,
                                                                        capsys):
    """⛔ P3 (PR #13210 review): `--salvage` is a MODE, not evidence of damage.

    `_journal_damage_lines` correctly prints nothing when the counters are zero,
    while the status text asserted *"this read skipped or cannot account for
    input"* over that same read — two statements about one read, one of them
    false, and a completion claim withdrawn from a read that supports it. The
    predicate is the COUNTERS (unknown when unreported, or when damage is
    non-zero), so a clean salvage read reports `fully_consumed: true` and says
    nothing about skipping.
    """
    journal = Journal(path=FIXTURE,
                      records=[{"type": "IngestStarted"}, {"type": "PointAdded"}],
                      segmented=False, salvage=True, corrupt=0, torn=0)
    monkeypatch.setattr(record_runner, "read_journal", lambda *a, **k: journal)
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=2, present=2))

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--salvage", "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal"]["salvage"] is True
    assert payload["completeness_unknown"] is False
    assert payload["damage_unreported"] is False
    assert payload["fully_consumed"] is True, payload

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--salvage"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "SALVAGE" in out
    assert "complete: UNKNOWN" not in out
    assert "damaged input" not in out
    assert "damage NOT REPORTED" not in out


def test_a_TORN_TAIL_alone_withdraws_the_completion_claim(monkeypatch, capsys, tmp_path):
    """⛔ P2 (PR #13275 review): the `torn` term was fenced by no fixture.

    A SIGKILL mid-append leaves a truncated LAST line, which the reader tolerates
    (S15) without raising and WITHOUT a mid-file damage count — so a journal of two
    complete records plus a torn third line reads as `records=2, corrupt=0,
    torn=1`. That is the commoner damage case, and it is the one where the `torn`
    term is the ONLY thing that makes the read unaccountable: drop it
    (`completeness_unknown = damage_unreported or bool(journal.corrupt)`) and the
    suite stays at 55 passed while `status` reports `fully_consumed: true` over a
    journal that may have lost its last record.

    This uses a REAL journal through `read_journal`, not a constructed `Journal`, so
    the counter's own plumbing is fenced too — a fixture that hand-set `torn=1`
    would keep passing if the reader stopped counting.
    """
    journal = tmp_path / "events.jsonl"
    good = json.dumps({"type": "IngestStarted"})
    journal.write_text(good + "\n" + good + "\n" + '{"type":"PointAd',
                       encoding="utf-8")
    read = record_runner.read_journal(journal)      # the STRICT read tolerates it
    assert len(read) == 2
    assert read.corrupt == 0, "a torn tail is not a mid-file damage count"
    assert read.torn == 1, f"the reader stopped counting torn tails: {read.torn}"

    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=2, present=2))
    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(journal), "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["journal"]["torn_tails"] == 1, payload
    assert payload["events_remaining"] == 0, payload
    assert payload["completeness_unknown"] is True
    assert payload["damage_unreported"] is False
    assert payload["fully_consumed"] is None, (
        "a torn tail may have truncated the LAST record, so the count reaching the "
        "checkpoint is not proof that the record holds every mutation")

    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(journal)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "damaged input: 0 mid-file line(s), 1 torn tail fragment(s)" in out
    assert "complete: UNKNOWN — this read SKIPPED damaged input" in out


def test_every_verb_discloses_the_damage_it_read(monkeypatch, capsys):
    """⛔ P3 (PR #13210 review): the helper's docstring claimed three verbs.

    `_journal_damage_lines` said it was factored so "the three verbs cannot
    drift", and `apply` — the WRITE verb, whose report is the one an operator
    keeps — never called it and printed nothing about a segmented journal. A
    docstring the code contradicts is the same class as the pinned reason the
    previous round's finding was about, so this drives all THREE renders over one
    damaged journal rather than asserting the claim.
    """
    damaged = {"path": "j", "records": 1, "segmented": True, "salvage": False,
               "corrupt_lines": None, "torn_tails": None}
    damage_line = record_runner._journal_damage_lines(damaged)
    assert damage_line and "damage NOT REPORTED" in damage_line[0]

    plan = record_runner.PlanReport(
        org_id="o", graph_id="g", journal=damaged, checkpoint=None, cursor=None,
        start_at=0, events_to_consume=0,
        totals={"events": 0, "statements": 0, "position_rows": 0,
                "events_by_type": {}})
    applied = record_runner.ApplyReport(
        org_id="o", graph_id="g", journal=damaged, checkpoint_before={},
        start_at=0)
    for verb, text in (("plan", record_runner._render_plan(plan)),
                       ("apply", record_runner._render_apply(applied))):
        assert "damage NOT REPORTED" in text, f"{verb} dropped the disclosure: {text}"

    # …and the third verb through its real CLI path, not its renderer alone.
    journal = Journal(path=FIXTURE, records=[{"type": "IngestStarted"}],
                      segmented=True, salvage=False, corrupt=None, torn=None)
    monkeypatch.setattr(record_runner, "read_journal", lambda *a, **k: journal)
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=1, present=1))
    rc = main(["status", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    assert "damage NOT REPORTED" in capsys.readouterr().out


def test_apply_help_says_it_refuses_allocate(capsys, monkeypatch):
    """⛔ P3 (PR #13210 review): the one place an operator learns what it does.

    `apply` registers `--allocate` ON PURPOSE (a refusal that names the allocator
    beats argparse's "unrecognized arguments"), but `--help` described the mode
    as available and said nothing about the refusal — so the help text argued
    with what the verb actually does.

    `COLUMNS` is widened because argparse ELIDES a subparser's help past the
    terminal width (`[--dsn ...`), which would make this row pass by rendering
    nothing rather than by the sentence being right.
    """
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit) as exc:
        main(["apply", "--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--allocate" in out
    assert "`apply` REFUSES it" in out, out
    # `plan` DOES accept it, so its help must not claim otherwise
    monkeypatch.setenv("COLUMNS", "200")
    with pytest.raises(SystemExit) as exc:
        main(["plan", "--help"])
    assert exc.value.code == 0
    assert "`plan` only" in capsys.readouterr().out


def test_plan_allocate_does_not_hand_over_an_acknowledgement(monkeypatch, capsys):
    """⛔ P2 (PR #13210): the only unguarded arm of the acknowledgement block.

    `apply --allocate` is refused UNCONDITIONALLY, so printing
    `--acknowledge-plan N` on this arm hands the operator a guaranteed refusal —
    the same defect the cut-over and plan-did-not-read arms already suppress.
    Before the fix (measured with a stubbed record, cursor=None):
    `to apply this plan:  --acknowledge-plan 39`, rc 0.
    """
    rc = main(["plan", "--org-id", "o", "--graph-id", "g",
               "--journal", str(FIXTURE), "--allocate"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "--acknowledge-plan" not in out
    assert "`apply` refuses `--allocate`" in out
    # the PLAN itself is still produced and useful — only the instruction is gone
    assert "batch 1" in out and "TOTAL: 39 events" in out


def test_status_does_not_accept_a_bound_it_cannot_apply(monkeypatch, capsys):
    """`status` runs no batch, so a bound there was accepted and IGNORED.

    An operator passing `status --limit 5` got the full remainder and no hint that
    the flag did nothing — a flag that cannot do anything is worse than an absent
    one, so it is a usage error (exit 2), which is a different answer from a
    refusal and says which of the two happened.

    ⛔ `--allocate` IS THE SAME DEFECT and was missed here (review P2, PR
    #13210): it was registered OUTSIDE the bounds group, so `status --allocate`
    exited 0 with byte-identical output — whether a graph is cut over is read
    from the `record_cursor` row, never selected by a flag. Pinning all three
    together is the point: this row is what makes the group's membership a
    property rather than a habit, so the next flag added there must answer the
    same question. `apply` keeps `--allocate` on purpose — it is refused there
    with a sentence that names the allocator, which is a better answer than
    argparse's "unrecognized arguments".
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=39, present=39))
    for flag in (("--limit", "5"), ("--batch-size", "5"), ("--allocate", None)):
        argv = [flag[0]] if flag[1] is None else [flag[0], flag[1]]
        with pytest.raises(SystemExit) as exc:
            main(["status", "--org-id", "o", "--graph-id", "g",
                  "--journal", str(FIXTURE), *argv])
        assert exc.value.code == EXIT_USAGE, flag
        assert "unrecognized arguments" in capsys.readouterr().err, flag


def _must_not_run(*a, **k):  # pragma: no cover - the gate must refuse first
    raise AssertionError("the fold ran behind a rejected invocation")


def test_a_resumed_run_reports_where_it_started(monkeypatch, capsys):
    """A partial record resumes at its checkpoint, and only folds the tail."""
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=20, present=20))
    monkeypatch.setattr(_replay_attr(), "replay", _fold_report_for)
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "19"])
    assert rc == EXIT_OK
    payload = capsys.readouterr().out
    assert "resumed at seq: 21" in payload
    assert "consumed: 19 events" in payload


def test_a_cut_over_graph_is_REFUSED_and_the_allocator_is_named(monkeypatch, capsys):
    """`#9190`'s refusal, surfaced with the remedy instead of as a driver error.

    On a cut-over graph the ordinal is the allocator's to issue, so an enumerated
    replay would store the same event at a second position with no error. The
    runner must not be the place that discovers this halfway through a batch.
    """
    _patched(monkeypatch, checkpoint=Checkpoint(), cursor=(4, "seeded"))
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "39"])
    assert rc == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "CUT OVER" in err and "record-allocator" in err


def test_apply_refuses_when_the_record_holds_positions_the_journal_lacks(monkeypatch,
                                                                       capsys):
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=99, present=99))
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_REFUSED
    assert "not the same journal" in capsys.readouterr().err


def _replay_attr():
    """The module the runner imports `replay` FROM, at call time.

    The runner deliberately imports `record_projection` lazily (a plan-only run
    must not need the module), so the patch target is the SOURCE module rather
    than `record_runner` — patching an attribute the runner never holds would be
    a test that proves nothing about the call the tool makes.
    """
    from tortoise import record_projection
    return record_projection


class _FoldReport:
    """What `replay` returns: a report, never a substitute for the failure.

    Built from the batch the runner actually handed over, so a test cannot pass
    by asserting a number the FOLD invented rather than the one it consumed.
    """

    def __init__(self, events: int = 0, statements: int = 0):
        self.events = events
        self.statements = statements


def _fold_report_for(batch, *a, **k):
    return _FoldReport(events=len(batch), statements=len(batch) * 3)


def test_apply_rolls_the_batch_back_and_claims_nothing_when_the_fold_raises(
        monkeypatch, capsys):
    """A batch that fails leaves the record untouched and is not counted."""
    conn = _patched(monkeypatch, checkpoint=Checkpoint())

    def boom(*a, **k):
        raise RuntimeError("statement 2/60 refused")
    monkeypatch.setattr(_replay_attr(), "replay", boom)

    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "39"])
    assert rc == EXIT_REFUSED
    # No batch COMMIT — the only commit this tool ever makes is a batch's. (A
    # read-only checkpoint closes with a rollback, deliberately.)
    assert conn.committed == 0
    assert conn.rolled_back >= 1
    assert "ROLLED BACK" in capsys.readouterr().err


def test_a_committed_batch_is_counted_from_the_replay_report_not_from_the_plan(
        monkeypatch, capsys):
    """`consumed`/`statements` come from what the fold did, not what it planned."""
    _patched(monkeypatch, checkpoint=Checkpoint())
    monkeypatch.setattr(_replay_attr(), "replay", _fold_report_for)
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "39"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    # 39 events is what the fold consumed; 117 statements is what IT reported
    # (3 per record), where the PLAN for this journal says 99 — so asserting the
    # fold's number is what proves the report did not come from the plan.
    assert "consumed: 39 events" in out and "117 statements" in out


# ── the low edge of the checkpoint (review P0) ──────────────────────────────


class _ScriptedCursor:
    """Answers `read_checkpoint`'s two reads from a script, in order."""

    def __init__(self, rows):
        self._rows = list(rows)
        self._current = None

    def execute(self, sql, params=None):
        self._current = self._rows.pop(0)

    def fetchone(self):
        return self._current


def _read_checkpoint_with(monkeypatch, *, aggregate, gap_row, allocated=False):
    """`read_checkpoint` with only its SQL answers stubbed — the DECISION is the subject."""
    from tortoise import record_projection

    fake = _ScriptedCursor([aggregate, gap_row])
    monkeypatch.setattr(record_projection, "scope_connection",
                        lambda conn, org_id: fake)
    return record_runner.read_checkpoint(None, "o", "g", allocated=allocated)


def test_a_record_whose_lowest_row_is_ABOVE_1_is_a_gap_at_position_1(monkeypatch):
    """⛔ THE P0: a record starting at 251 has consumed NONE of the journal's prefix.

    The gap query can only see a hole BETWEEN two rows (it looks at a `lead()`
    neighbour), so a record armed after the journal already held 250 lines — the
    ordinary shape for this whole lane, since `journal_event.seq` is only
    `CHECK (seq > 0)` — read as `contiguous` with `next_seq = 252`. `apply` then
    folded the tail and reported success over a record that had never seen
    records 1..250: exactly the historic payload this tool exists to move,
    reported as done.
    """
    cp = _read_checkpoint_with(monkeypatch, aggregate=(300, 50, 251), gap_row=(None,))
    assert cp.min_seq == 251
    assert cp.first_gap == 1, "the unfolded PREFIX must be the resume point"
    assert cp.next_seq == 1
    assert cp.contiguous is False


def test_a_hole_BETWEEN_rows_still_wins_over_the_low_edge(monkeypatch):
    cp = _read_checkpoint_with(monkeypatch, aggregate=(10, 9, 1), gap_row=(4,))
    assert cp.first_gap == 4 and cp.next_seq == 4


def test_a_record_starting_at_1_with_no_holes_is_contiguous(monkeypatch):
    cp = _read_checkpoint_with(monkeypatch, aggregate=(10, 10, 1), gap_row=(None,))
    assert cp.contiguous is True and cp.next_seq == 11


def test_an_allocated_graph_may_legitimately_start_above_1(monkeypatch):
    """The allocator's seed floor is not a gap — and only the CALLER may say so."""
    cp = _read_checkpoint_with(monkeypatch, aggregate=(600, 101, 500),
                               gap_row=(None,), allocated=True)
    assert cp.first_gap is None and cp.next_seq == 601


def test_an_empty_record_resumes_at_position_1(monkeypatch):
    cp = _read_checkpoint_with(monkeypatch, aggregate=(0, 0, 0), gap_row=None)
    assert cp.next_seq == 1 and cp.contiguous is True


# ── legs the first cut of this suite was missing (review P2) ────────────────


def test_apply_folds_ACROSS_BATCHES_and_counts_every_one(monkeypatch, capsys):
    """The per-batch loop, executed: 39 records at `--batch-size 10` is 4 batches.

    The first cut of this suite never ran `apply` over more than one batch (the
    fixture fits in the default 200), so the whole loop — plan-before-write,
    per-batch commit, accumulation — was untested, which is how the `--allocate`
    multi-batch defect shipped.
    """
    conn = _patched(monkeypatch, checkpoint=Checkpoint())
    monkeypatch.setattr(_replay_attr(), "replay", _fold_report_for)

    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--batch-size", "10", "--acknowledge-plan", "39"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert conn.committed == 4, "one commit per batch, and there are four batches"
    assert "consumed: 39 events in 4 batch(es)" in out
    assert "seq 1..39" in out


def test_the_write_ledger_is_a_MEASUREMENT_the_fold_populates(monkeypatch):
    """The ledger a plan reports as empty is the one `apply` fills — so reachable.

    Without this, "the plan's ledger is empty" is a statement about a field
    nothing ever writes, which is a constant wearing a field's name. 39 records
    at `--batch-size 10` folds as 4 batches → the fold reports 39 events and
    117 statements (3 per record), both non-zero.
    """
    monkeypatch.setattr(_replay_attr(), "replay", _fold_report_for)
    journal = record_runner.read_journal(FIXTURE)
    report = record_runner.apply_batches(
        journal=journal, org_id="o", graph_id="g", conn=_StubConn(),
        start_at=0, batch_size=10, limit=None, allocate=False,
        timeout_ms=1000, echo=lambda line: None)
    assert report.executed.rows_written == 39, report.executed
    assert report.executed.statements_executed == 117, report.executed


def test_plan_from_checkpoint_opens_ONE_read_only_connection(monkeypatch, capsys):
    """The branch that DOES open a connection must still write nothing."""
    conn = _patched(monkeypatch, checkpoint=Checkpoint(high_seq=20, present=20),
                    cursor=None)
    rc = main(["plan", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--dsn", "postgresql://x/y", "--from-checkpoint"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert conn.committed == 0, "a plan may not commit"
    assert conn.rolled_back >= 1
    assert "resume at seq 21" in out
    # …and now the acknowledgement line IS the right one for this run
    assert "--acknowledge-plan 19" in out


def test_plan_reports_CUT_OVER_and_does_not_hand_over_an_acknowledgement(
        monkeypatch, capsys):
    """`report.cursor` was hardcoded None, so this line was unreachable.

    And the checkpoint it uses is an ALLOCATOR position, deliberately above the
    journal's length: on the allocated arm `high_seq` is not an index into this
    journal, so the journal-length guard must not fire — before this, a correctly
    seeded graph was refused with "they are not the same journal".
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=502, present=3, min_seq=500),
             cursor=(600, "seeded"))
    rc = main(["plan", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--dsn", "postgresql://x/y", "--from-checkpoint"])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "cutover: cursor present" in out and "REFUSED" in out
    # ⛔ The test's NAME claims this; before, only the cutover line was asserted,
    # so the ack number could (and did) come back without any test noticing.
    assert "--acknowledge-plan" not in out


def test_status_reports_the_remainder_on_the_enumerated_arm(monkeypatch, capsys):
    """`status` had NO coverage, which is why the P1 below reached a head."""
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=20, present=20))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "events remaining: 19" in out


def test_status_refuses_to_subtract_an_ALLOCATOR_ordinal_from_a_journal_length(
        monkeypatch, capsys):
    """⛔ The P1: two coordinate systems, and `status` reported a false DONE.

    On a cut-over graph `journal_event.seq` is an ordinal the COUNTER issued, so
    `checkpoint.next_seq` (600) is not an index into this 39-record journal. The
    old arithmetic `len(journal) - (next_seq - 1)` therefore went NEGATIVE and
    clamped to 0, printing `events_remaining: 0` and `fully_consumed: true` for a
    record holding 3 ordinals and NONE of this journal's records — while
    `plan` on the same graph correctly says the fold does not apply.
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=600, present=3, min_seq=500),
             cursor=(600, "seeded"))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["events_remaining"] is None, (
        "a remainder was computed across two coordinate systems")
    assert payload["fully_consumed"] is None, (
        "a cut-over graph must never be reported as fully consumed")
    assert payload["cursor"] == {"seq": 600, "mode": "seeded"}


def test_status_text_says_UNKNOWN_rather_than_a_false_zero(monkeypatch, capsys):
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=600, present=3, min_seq=500),
             cursor=(600, "seeded"))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "events remaining: unknown" in out
    assert "events remaining: 0" not in out
    assert "CUT OVER" in out


def test_status_does_not_report_a_completion_over_a_DIFFERENT_journal(
        monkeypatch, capsys):
    """⛔ ENUMERATED arm, positions past the journal's length — the P2 residual.

    `plan --from-checkpoint` REFUSES this input by name ("they are not the same
    journal"). `status` clamped `39 - 100` to 0 and printed
    `fully_consumed: true` — asserting completion about two stores that hold
    different histories. Two coordinate systems must not be reconciled into
    "done" by arithmetic on either arm.
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=100, present=100,
                                                first_gap=None, min_seq=1))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "not the same journal" in out
    assert "events remaining: 0" not in out
    assert "fully_consumed: true" not in out

    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["events_remaining"] is None
    assert payload["fully_consumed"] is None


def test_status_at_the_COMPLETION_BOUNDARY_still_reports_done(monkeypatch, capsys):
    """⛔ The mirror of the test above: the mismatch guard must not OVER-FIRE.

    At `next_seq - 1 == len(journal.records)` the record holds exactly this
    journal and the honest answer is `0` / `true`. A guard written `>=` instead
    of `>` reports `unknown` for a correctly completed record — a false refusal
    on the one input where the tool is supposed to say "done".

    ⛔ Both sides are pinned AT THE EXACT VALUE — this test at `len`, the next at
    `len + 1` — and both derive `len` from the fixture rather than hardcoding 39: a
    test sitting only a few positions away lets a guard LOOSENED to `> len + k`
    stay green while it reports `fully_consumed: true` for a record that holds
    positions this journal does not have (measured: `> len + 5` survived until
    the `len + 1` case below existed).
    """
    n = len(read_journal(FIXTURE))
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=n, present=n,
                                                first_gap=None, min_seq=1))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "events remaining: 0" in out
    assert "not the same journal" not in out

    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["events_remaining"] == 0
    assert payload["fully_consumed"] is True


def test_status_ONE_position_past_the_boundary_is_a_mismatch(monkeypatch, capsys):
    """⛔ The under-fire side, pinned at `len + 1` — not a few positions away.

    A record holding one position more than this journal has is already a
    DIFFERENT journal, and `plan` refuses it. Measured before this test: a guard
    loosened to `> len + 5` left the suite green and reported
    `events remaining: 0` / `fully_consumed: true` for records holding up to 44
    positions over this 39-record journal.
    """
    n = len(read_journal(FIXTURE))
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=n + 1, present=n + 1,
                                                first_gap=None, min_seq=1))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "not the same journal" in out
    assert "events remaining: 0" not in out

    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--json"])
    assert rc == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["events_remaining"] is None
    assert payload["fully_consumed"] is None


def test_the_mismatch_sentence_names_the_HIGH_water_mark_not_the_resume_point(
        monkeypatch, capsys):
    """⛔ The sentence must equal `plan`'s for the same record (review P2).

    `Checkpoint.next_seq` is `first_gap` whenever a gap exists, so
    `next_seq - 1` is NOT what the record holds — with a gap at 50 it printed
    "positions up to 49" while `plan` said "up to 100". A refusal diagnostic
    that under-states the record is its own defect on a refusal path.
    """
    _patched(monkeypatch, checkpoint=Checkpoint(high_seq=100, present=99,
                                                first_gap=50, min_seq=1))
    rc = main(["status", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE)])
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "positions up to 100" in out
    assert "positions up to 49" not in out


def test_apply_REFUSES_allocate_because_it_would_mint_fresh_ordinals(
        monkeypatch, capsys):
    """The allocation arm cannot carry a historic fold: it issues NEW positions."""
    _patched(monkeypatch, checkpoint=Checkpoint(), cursor=(0, "active"))
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--allocate", "--acknowledge-plan", "39"])
    assert rc == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "--allocate" in err and "seed" in err


def test_apply_REFUSES_salvage_because_a_skipped_line_SHIFTS_positions(
        monkeypatch, capsys):
    _patched(monkeypatch, checkpoint=Checkpoint())
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--salvage", "--acknowledge-plan", "39"])
    assert rc == EXIT_REFUSED
    assert "SHIFTS" in capsys.readouterr().err


def test_plan_allocate_accounts_the_allocation_statement_as_POSITIONS(monkeypatch,
                                                                     capsys):
    """⛔ The allocation statement writes positions — it is not a mutation.

    Its SQL begins `WITH payloads AS (`, so it does not match the journal-insert
    prefix, and the first cut counted it as a mutation while counting none of the
    positions it writes: `1 statements, 0 position rows` for a 3-record batch.
    `ReplayReport` skips the allocation prefix, so plan and run disagreed by one
    per batch.
    """
    from record_runner import plan_batch

    planned = plan_batch([{"type": "IngestStarted"} for _ in range(3)],
                         start_seq=1, org_id="o", graph_id="g", allocate=True)
    assert planned.statements == 0, "an allocation batch is ONE position write"
    assert planned.position_rows == 1


# ── the CLI contract ─────────────────────────────────────────────────────────


def test_usage_errors_are_distinct_from_refusals():
    with pytest.raises(SystemExit) as exc:
        main(["apply", "--org-id", "o"])          # missing required args
    assert exc.value.code == EXIT_USAGE


def test_the_runner_opens_no_graph_connection_and_issues_no_cypher():
    """⛔ `#10175` / `#5333`: nothing here may delete, prune or touch the graph.

    Executed as a scan of this tool's own source, because the property is
    "this file cannot reach a graph" — not "this run happened not to".
    """
    source = (REPO_ROOT / "tools" / "record_runner.py").read_text(encoding="utf-8")
    for forbidden in ("MATCH (", "DETACH DELETE", "DELETE ", "falkordb",
                      "FalkorProjection", "TORTOISE_DB_URI"):
        assert forbidden not in source, (
            f"tools/record_runner.py mentions {forbidden!r}: this tool must not "
            f"be able to reach the graph (#10175 / #5333)"
        )
