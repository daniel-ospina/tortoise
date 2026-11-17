"""#13331 — the LIVE write chokepoint, the journal-coverage leg, and the dense gate.

DB-free. The three surfaces this issue adds are all decisions rather than SQL:

1. `tortoise.live_write.write` — append → record → cache, in that order, with the
   outcome NAMED when the record did not take the write;
2. `record_projection.journal_coverage` / `RecordReconcileResult` — the
   disagreement counted and verdict-ed, with "not measured" distinguishable from
   "measured zero";
3. `record_vector.journal_ready` — the per-tenant condition on the dense leg's
   deployment switch.

The live-DB falsifiers (a product-door write readable from the record with no
backfill; a forced projection failure; an idempotent retry; the cutover refusal)
are driven on real containers and recorded in the PR — they are not folded in
here, because a test that needs Postgres cannot run in every lane.
"""
from __future__ import annotations

import types

import pytest

from tortoise import live_write
from tortoise.record_projection import JournalCoverage
from tortoise.record_reconcile import (
    EXIT_CLEAN,
    EXIT_DIVERGENT,
    VERDICT_CLEAN,
    VERDICT_UNPROJECTED,
    RecordReconcileResult,
    exit_code_for,
    log_result,
    reconcile_record_and_cache,
)
from tortoise.reconciliation import ReconciliationReport
from tortoise.residency import ResidencyDeclaration


# ── helpers ────────────────────────────────────────────────────────────────


class _Log:
    """An `EventLog`-like double that records the order of its own calls."""

    durable = True

    def __init__(self, seq: int | None = 7, order: list | None = None,
                 raises: BaseException | None = None):
        self.seq = seq
        self.order = order if order is not None else []
        self.raises = raises
        self.appended: list = []

    def append(self, event):
        self.order.append("append")
        if self.raises is not None:
            raise self.raises
        self.appended.append(event)
        return self.seq


class _Projection:
    def __init__(self, order: list | None = None):
        self.order = order if order is not None else []
        self.applied: list = []

    def apply(self, event):
        self.order.append("fold")
        self.applied.append(event)


def _outcome(*, applied: bool, refusal: str | None = None, outcome: str = "applied"):
    return types.SimpleNamespace(applied=applied, refusal=refusal, outcome=outcome)


def _record(project, order):
    def call(event, seq):
        order.append("project")
        return project(event, seq)
    return call


# ── 1. the chokepoint's ORDER ──────────────────────────────────────────────


def test_append_then_record_then_fold_is_the_only_order_produced():
    """S2's contract: journal → record → cache. One call, one order."""
    order: list = []
    log = _Log(order=order)
    projection = _Projection(order=order)

    result = live_write.write(
        {"type": "PointAdded"}, log=log, projection=projection,
        project=_record(lambda e, s: _outcome(applied=True), order))

    assert order == ["append", "project", "fold"]
    assert result.applied is True
    assert result.folded is True
    assert result.unprojected == 0
    assert log.appended == [{"type": "PointAdded"}]


def test_the_record_is_handed_the_append_s_own_ordinal():
    """The ordinal is the journal's fact (#9190), never a number minted here."""
    seen = {}
    log = _Log(seq=41)

    def project(event, seq):
        seen["seq"] = seq
        return _outcome(applied=True)

    result = live_write.write({"type": "PointAdded"}, log=log, project=project)

    assert seen["seq"] == 41
    assert result.seq == 41


def test_a_failed_append_propagates_and_nothing_else_runs():
    """The append is the commit point: no record, no fold, no swallowed error."""
    order: list = []
    boom = OSError("disk on fire")
    log = _Log(order=order, raises=boom)

    with pytest.raises(OSError):
        live_write.write({"type": "PointAdded"}, log=log, projection=_Projection(order),
                         project=_record(lambda e, s: _outcome(applied=True), order))

    assert order == ["append"]


def test_exactly_one_of_log_or_append_is_required():
    with pytest.raises(TypeError):
        live_write.write({"type": "x"})
    with pytest.raises(TypeError):
        live_write.write({"type": "x"}, log=_Log(), append=lambda: 1)


# ── 2. the NAMED disagreement ──────────────────────────────────────────────


def test_a_refused_record_is_a_named_unprojected_disagreement_and_the_fold_still_runs():
    """A record refusal must not cost the cache write, and must be reportable."""
    order: list = []
    log = _Log(order=order)
    projection = _Projection(order=order)
    refused = _outcome(applied=False, refusal="write_failed", outcome="refused")

    result = live_write.write({"type": "PointAdded"}, log=log,
                              projection=projection,
                              project=_record(lambda e, s: refused, order))

    assert result.unprojected == 1
    assert result.refusal == "write_failed"
    assert result.applied is False
    assert order == ["append", "project", "fold"]
    assert projection.applied == [{"type": "PointAdded"}]


def test_a_raising_projection_is_reported_not_raised_after_the_append():
    """⛔ never raise after the commit point — and still fold the cache."""
    order: list = []
    log = _Log(order=order)
    projection = _Projection(order=order)

    def project(event, seq):
        order.append("project")
        raise RuntimeError("record is down")

    result = live_write.write({"type": "PointAdded"}, log=log,
                              projection=projection, project=project)

    assert result.unprojected == 1
    assert result.refusal == "projection_raised"
    assert order == ["append", "project", "fold"]
    assert len(projection.applied) == 1


def test_an_unarmed_record_is_not_an_unprojected_disagreement():
    """`None` means nothing claimed the write (unarmed / declined)."""
    order: list = []
    result = live_write.write({"type": "PointAdded"}, log=_Log(order=order),
                              project=_record(lambda e, s: None, order))

    assert result.record is None
    assert result.armed is False
    assert result.unprojected == 0
    assert result.refusal is None


def test_the_record_outcome_travels_to_the_capture_sink():
    """#10000's per-call carrier keeps working through the chokepoint."""
    sink: list = []
    out = _outcome(applied=True)

    live_write.write({"type": "PointAdded"}, log=_Log(),
                     project=lambda e, s: out, record_sink=sink)

    assert sink == [out]


def test_fold_false_leaves_the_fold_to_the_door():
    projection = _Projection()
    live_write.write({"type": "PointAdded"}, log=_Log(), projection=projection,
                     fold=False, project=lambda e, s: _outcome(applied=True))
    assert projection.applied == []


# ── 3. journal coverage: the number, and its two absences ──────────────────


def _coverage(present, high, low, within, gap=None, enumerated=None):
    return JournalCoverage(present=present, high_seq=high, min_seq=low,
                           within_prefix=within, first_gap=gap,
                           enumerated=enumerated)


@pytest.mark.parametrize("cov,expected", [
    (_coverage(3, 3, 1, 3, None, 3), 0),
    (_coverage(2, 3, 1, 2, 2, 3), 1),
    (_coverage(2, 3, 2, 2, None, 3), 1),          # a missing PREFIX
    (_coverage(0, 0, 0, 0, None, 5), 5),          # the backfill never ran
    (_coverage(5, 5, 1, 5, None, None), None),    # not measured
])
def test_unprojected_is_measured_never_guessed(cov, expected):
    assert cov.unprojected == expected


def test_contiguous_covers_both_edges():
    assert _coverage(3, 3, 1, 3).contiguous is True
    assert _coverage(2, 3, 1, 2, gap=2).contiguous is False
    assert _coverage(2, 3, 2, 2).contiguous is False   # starts above 1


def _result(*, journal=None, divergent=0, checked=1):
    return RecordReconcileResult(
        report=ReconciliationReport(checked=checked, divergent=divergent,
                                    missing_in_cache=divergent,
                                    extra_in_cache=0,
                                    field_mismatches=0),
        declaration=ResidencyDeclaration(source=None),
        journal=journal,
    )


def test_the_journal_leg_has_its_own_verdict_and_exit_code():
    lagging = _result(journal=_coverage(2, 3, 1, 2, 2, 3))

    assert lagging.verdict() == VERDICT_UNPROJECTED
    assert exit_code_for(lagging) == EXIT_DIVERGENT
    # a lagging record is NOT the cache-divergence diagnosis
    assert lagging.unprojected_journal == 1


def test_a_coherent_record_is_clean_and_says_the_journal_leg_was_measured():
    ok = _result(journal=_coverage(3, 3, 1, 3, None, 3))
    assert ok.verdict() == VERDICT_CLEAN
    assert exit_code_for(ok) == EXIT_CLEAN
    assert ok.as_dict()["unprojected_journal"] == 0
    assert ok.as_dict()["journal"]["enumerated"] == 3


def test_no_journal_leg_is_null_not_zero():
    """`0` must never be reported for a check that did not run (#5090 Q2)."""
    unmeasured = _result()
    assert unmeasured.as_dict()["journal"] is None
    line = log_result(unmeasured)
    assert "journal:" not in line


def test_a_cache_divergence_outranks_a_lagging_record():
    both = _result(journal=_coverage(2, 3, 1, 2, 2, 3), divergent=1)
    assert both.verdict() != VERDICT_UNPROJECTED


def test_the_rendered_line_names_the_lag_and_its_repair():
    line = log_result(_result(journal=_coverage(2, 3, 1, 2, 2, 3)))
    assert "verdict=unprojected_journal" in line
    assert "record_runner.py apply" in line
    assert "1 of 3" in line
    # ⛔ and it must NOT promise that heal on a cut-over graph, where the
    # enumerated replay is refused by design (#9190, #13338).
    assert "#13338" in line
    assert "CUT-OVER" in line


def test_passing_the_journal_leg_through_the_pure_composition():
    result = reconcile_record_and_cache(
        {"k": {"v": 1}}, {"k": {"v": 1}},
        journal=_coverage(1, 1, 1, 1, None, 1))
    assert result.unprojected_journal == 0
    assert result.verdict() == VERDICT_CLEAN


# ── 4. the dense leg's per-tenant gate ─────────────────────────────────────


@pytest.fixture()
def vector(monkeypatch):
    from tortoise import record_vector

    record_vector.reset_readiness_cache()
    monkeypatch.setattr(record_vector, "_WARNED_UNDECLARED", False)
    yield record_vector
    record_vector.reset_readiness_cache()


def test_the_gate_cannot_run_without_a_declared_journal_and_says_so(
        vector, monkeypatch, caplog):
    monkeypatch.delenv(vector.RECORD_JOURNAL_ENV, raising=False)
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")

    assert vector.journal_ready(scope) is True       # the switch decides, as before
    # …and the absence is LOUD, once per process
    assert any("cannot run" in rec.message for rec in caplog.records)
    caplog.clear()
    assert vector.journal_ready(scope) is True
    assert not any("cannot run" in rec.message for rec in caplog.records)


def test_a_tenant_whose_record_holds_its_journal_is_ready(vector, monkeypatch, tmp_path):
    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n")
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(journal))
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")

    asked = {}

    def fake_measure(scope, *, record_dsn=None):
        asked["called"] = True
        return True

    monkeypatch.setattr(vector, "_measure_readiness", fake_measure)
    assert vector.journal_ready(scope) is True
    assert asked["called"] is True


def test_the_readiness_verdict_is_cached_for_the_ttl(vector, monkeypatch):
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, "/tmp/whatever/events.jsonl")
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")
    calls = []

    def fake_measure(scope, *, record_dsn=None):
        calls.append(1)
        return len(calls) == 1        # ready, then not

    monkeypatch.setattr(vector, "_measure_readiness", fake_measure)
    assert vector.journal_ready(scope, now=100.0) is True
    assert vector.journal_ready(scope, now=110.0) is True     # cached
    assert vector.journal_ready(scope, now=100.0 + vector.READINESS_TTL_SECONDS + 1) is False
    assert len(calls) == 2


def test_a_lagging_tenant_keeps_the_graph_branch(vector, monkeypatch):
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, "/tmp/whatever/events.jsonl")
    monkeypatch.setattr(vector, "_measure_readiness",
                        lambda scope, *, record_dsn=None: False)
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")
    assert vector.journal_ready(scope) is False


def test_an_unreadable_journal_is_not_ready(vector, monkeypatch, tmp_path):
    """Fail-closed: a check that could not run never reads as ready."""
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(tmp_path / "nope" / "events.jsonl"))
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")
    assert vector._measure_readiness(scope, record_dsn=None) is False
