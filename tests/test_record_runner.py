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
idempotent. Those are the reasons `FOLD-*` (the live leg at the bottom) exists and
the reasons the PR body carries a real fold's output rather than this suite's.
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
    EXIT_OK, EXIT_REFUSED, EXIT_USAGE,
    Checkpoint, Journal, RunnerError, batch_slices, build_plan, main,
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
    """A mistyped path must not read as "nothing to migrate" (the vacuity class)."""
    with pytest.raises(RunnerError) as exc:
        read_journal(tmp_path / "nope.jsonl")
    assert "does not exist" in str(exc.value)


def test_strict_reading_refuses_mid_file_damage_and_salvage_counts_it(tmp_path):
    """`salvage` is an explicit choice, and the report says when it was taken.

    A fold that SKIPPED a line would produce a record missing a mutation and
    report success — so the strict read must raise, and the salvaging read must
    carry its own damage count into the report rather than looking clean.
    """
    journal = tmp_path / "events.jsonl"
    good = json.dumps({"type": "IngestStarted", "ts": "2026-01-01T00:00:00Z"})
    journal.write_text(good + "\n{not json\n" + good + "\n", encoding="utf-8")

    with pytest.raises(Exception):
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

    with pytest.raises(Exception) as exc:
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
    assert "--acknowledge-plan 39" in out


def test_plan_json_reports_zero_writes_and_the_event_census(capsys):
    assert main(["plan", "--org-id", "o", "--graph-id", "g",
                 "--journal", str(FIXTURE), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows_written"] == 0 and payload["statements_executed"] == 0
    assert payload["events_to_consume"] == 39
    assert payload["totals"]["events_by_type"]["PointAdded"] >= 1


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
    """A journal that grew since the plan means the plan describes a different run."""
    _patched(monkeypatch, checkpoint=Checkpoint())
    rc = main(["apply", "--org-id", "o", "--graph-id", "g", "--journal", str(FIXTURE),
               "--acknowledge-plan", "7"])
    assert rc == EXIT_REFUSED
    err = capsys.readouterr().err
    assert "7" in err and "39" in err


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
