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
from tortoise.reconciliation import ReconciliationReport
from tortoise.record_projection import JournalCoverage
from tortoise.record_reconcile import (
    EXIT_CLEAN,
    EXIT_DIVERGENT,
    EXIT_UNAVAILABLE,
    VERDICT_CLEAN,
    VERDICT_UNANSWERABLE,
    VERDICT_UNPROJECTED,
    RecordReconcileResult,
    exit_code_for,
    log_result,
    reconcile_record_and_cache,
)
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
    payload = unmeasured.as_dict()
    assert payload["journal"] is None
    # ⛔ AND THE COUNT, not only the reading (#13331 review F2): the property
    # answers `0` so the verdict arithmetic stays total, but a `0` on the WIRE is
    # a MEASURED zero — `record-reconcile --json` without `--journal` then tells
    # a scheduler the record holds the whole journal, which is the silent green
    # this module refuses everywhere else. RED on the pre-fix code, which wrote
    # `self.unprojected_journal` here.
    assert payload["unprojected_journal"] is None, (
        "an unmeasured journal leg reported a COUNT: 0 on the wire is "
        "indistinguishable from 'measured, and the record is complete'")
    assert unmeasured.unprojected_journal == 0   # in memory: the verdict stays total
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
    """The composition must CARRY the leg — a coherent fixture proves nothing.

    ⛔ A LAGGING coverage on purpose (#13331 review F4). The earlier cut of this
    test used a coherent one, where `unprojected_journal == 0` and
    `verdict() == clean` hold IDENTICALLY with the `journal=` argument dropped:
    the mutant (`journal=journal,` deleted from the `RecordReconcileResult(...)`
    construction) left the suite green, so the one assertion claiming to test the
    threading tested nothing. With lagging coverage the same deletion falls to
    `journal is None`, i.e. `0` / `clean` — red.
    """
    result = reconcile_record_and_cache(
        {"k": {"v": 1}}, {"k": {"v": 1}},
        journal=_coverage(2, 3, 1, 2, 2, 3))
    assert result.journal is not None, "the leg was not carried into the result"
    assert result.unprojected_journal == 1
    assert result.verdict() == VERDICT_UNPROJECTED
    assert result.as_dict()["unprojected_journal"] == 1
    assert exit_code_for(result) == EXIT_DIVERGENT


def test_the_cli_threads_the_journal_leg_it_measured(monkeypatch, tmp_path):
    """`record-reconcile --journal <path>` must PASS ON the leg it read.

    ⛔ The neighbouring wiring had NO assertion at all (#13331 review F4):
    `_cmd_record_reconcile` builds `journal_leg = _coverage(...)` and hands it to
    the composition, and deleting those eight lines left every test in the repo
    green while `record-reconcile` silently stopped reporting the third leg. This
    pin drives the VERB (not the composition) and asserts the leg arrived.

    DB-free: the record and cache sides are stubs, the journal is a real file.
    """
    import sys
    import types as _types

    import tortoise.cache_projection as cache_projection
    import tortoise.projection as projection
    import tortoise.record_projection as record_projection
    import tortoise.record_reconcile as record_reconcile
    import tortoise.record_writer as record_writer
    import tortoise.residency as residency
    from tortoise.__main__ import _cmd_record_reconcile

    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n{}\n")            # enumerated == 3

    seen: dict = {}
    real_compose = record_reconcile.reconcile_record_and_cache

    def spy_compose(record, cache, **kwargs):
        seen.update(kwargs)
        return real_compose(record, cache, **kwargs)

    class _Conn:
        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "psycopg2",
                        _types.SimpleNamespace(connect=lambda *a, **k: _Conn()))
    monkeypatch.setattr(record_writer, "record_dsn",
                        lambda dsn=None: "postgresql://localhost/record")
    monkeypatch.setattr(cache_projection, "read_record_view",
                        lambda conn, *, org_id, graph_id: {"k": {"v": 1}})
    monkeypatch.setattr(record_reconcile, "record_key_map",
                        lambda view: dict(view))
    monkeypatch.setattr(record_reconcile, "scan_cache_view",
                        lambda g, *, timeout_ms: _types.SimpleNamespace(
                            view={"k": {"v": 1}}, placed={}))
    monkeypatch.setattr(record_reconcile, "reconcile_record_and_cache",
                        spy_compose)
    monkeypatch.setattr(residency, "read_declaration",
                        lambda g: ResidencyDeclaration(source=None))
    monkeypatch.setattr(projection, "FalkorProjection",
                        _types.SimpleNamespace(
                            from_uri=lambda uri, graph_name=None: _types.SimpleNamespace(
                                g=object(), close=lambda: None)))
    # the record-side coverage reader: this pin is the THREADING, not the SQL
    monkeypatch.setattr(record_projection, "journal_coverage",
                        lambda conn, *, org_id, graph_id, enumerated=None,
                        want_gap=True: _coverage(2, 3, 1, 2, 2, enumerated))

    args = _types.SimpleNamespace(
        record_dsn=None, db="redis://unused", cache_graph="org_o",
        org="o", graph="g", journal=str(journal), timeout_ms=None, json=False)
    rc = _cmd_record_reconcile(args)

    assert seen.get("journal") is not None, (
        "the CLI measured the journal leg and did NOT pass it on — the third "
        "leg is unreachable from the production trigger")
    assert seen["journal"].unprojected == 1
    assert rc == EXIT_DIVERGENT


@pytest.mark.parametrize("shape", ["missing", "directory"])
def test_the_cli_refuses_a_journal_it_could_not_count(
        shape, monkeypatch, tmp_path):
    """An uncountable `--journal` is `unavailable` (2), never a clean pass.

    ⛔ `EventLog.position()` answers `0` for a path that is not there, so the
    verb used to render `unprojected_journal: 0` and print the unqualified
    `✓ record == cache` — exit 0 — for a mistyped path on a tenant that may be
    far behind its journal. An unreadable-but-existing path (a directory, a
    rotation race) escaped as a traceback instead, while the verb's own
    docstring promises `1` is never returned and `2` means "no verdict was
    produced" (#13331 review F3 / review B).
    """
    import sys
    import types as _types

    import tortoise.cache_projection as cache_projection
    import tortoise.projection as projection
    import tortoise.record_reconcile as record_reconcile
    import tortoise.record_writer as record_writer
    import tortoise.residency as residency
    from tortoise.__main__ import _cmd_record_reconcile

    target = (tmp_path / "absent" / "events.jsonl" if shape == "missing"
              else tmp_path)

    composed: list = []

    class _Conn:
        def close(self):
            pass

    monkeypatch.setitem(sys.modules, "psycopg2",
                        _types.SimpleNamespace(connect=lambda *a, **k: _Conn()))
    monkeypatch.setattr(record_writer, "record_dsn",
                        lambda dsn=None: "postgresql://localhost/record")
    monkeypatch.setattr(cache_projection, "read_record_view",
                        lambda conn, *, org_id, graph_id: {"k": {"v": 1}})
    monkeypatch.setattr(record_reconcile, "record_key_map",
                        lambda view: dict(view))
    monkeypatch.setattr(record_reconcile, "scan_cache_view",
                        lambda g, *, timeout_ms: _types.SimpleNamespace(
                            view={"k": {"v": 1}}, placed={}))
    monkeypatch.setattr(
        record_reconcile, "reconcile_record_and_cache",
        lambda record, cache, **kw: composed.append(kw) or pytest.fail(
            "a journal that could not be counted was reconciled as though it had "
            "been measured — the run reports a coverage it never established"))
    monkeypatch.setattr(residency, "read_declaration",
                        lambda g: ResidencyDeclaration(source=None))
    monkeypatch.setattr(projection, "FalkorProjection",
                        _types.SimpleNamespace(
                            from_uri=lambda uri, graph_name=None:
                            _types.SimpleNamespace(g=object(), close=lambda: None)))

    args = _types.SimpleNamespace(
        record_dsn=None, db="redis://unused", cache_graph="org_o",
        org="o", graph="g", journal=str(target), timeout_ms=None, json=False)

    assert _cmd_record_reconcile(args) == EXIT_UNAVAILABLE, (
        "an uncountable journal produced a verdict: `2` is 'no verdict was "
        "produced', and `0` here would tell a scheduler the record holds every "
        "journal position on the strength of a count that never ran")
    assert composed == []


# ── 4. the dense leg's per-tenant gate ─────────────────────────────────────


@pytest.fixture()
def vector(monkeypatch):
    from tortoise import record_vector

    record_vector.reset_readiness_cache()
    monkeypatch.setattr(record_vector, "_WARNED_UNDECLARED", False)
    monkeypatch.setattr(record_vector, "_WARNED_MULTITENANT", False)
    yield record_vector
    record_vector.reset_readiness_cache()


@pytest.fixture()
def single_tenant(monkeypatch):
    """The declaration the journal gate requires (#9192, #13331 review F4).

    `TORTOISE_RECORD_JOURNAL` is a process-global PATH, so it describes one
    tenant; `journal_ready` refuses to apply it on a process that did not
    declare single tenancy. Every test that reaches the MEASUREMENT therefore
    declares it — and the test that must NOT (`…refuses_a_process_global_journal`)
    simply leaves it unset.
    """
    from tortoise.record_writer import RECORD_SINGLE_TENANT_ENV

    monkeypatch.setenv(RECORD_SINGLE_TENANT_ENV, "1")
    return RECORD_SINGLE_TENANT_ENV


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


def test_a_tenant_whose_record_holds_its_journal_is_ready(
        vector, single_tenant, monkeypatch, tmp_path):
    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n")
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(journal))
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")

    asked = {}

    def fake_measure(scope, *, record_dsn=None, timeout_ms=None):
        asked["called"] = True
        return True

    monkeypatch.setattr(vector, "_measure_readiness", fake_measure)
    assert vector.journal_ready(scope) is True
    assert asked["called"] is True


def test_the_readiness_verdict_is_cached_for_the_ttl(vector, single_tenant, monkeypatch):
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, "/tmp/whatever/events.jsonl")
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")
    calls = []

    def fake_measure(scope, *, record_dsn=None, timeout_ms=None):
        calls.append(1)
        return len(calls) == 1        # ready, then not

    monkeypatch.setattr(vector, "_measure_readiness", fake_measure)
    assert vector.journal_ready(scope, now=100.0) is True
    assert vector.journal_ready(scope, now=110.0) is True     # cached
    assert vector.journal_ready(scope, now=100.0 + vector.READINESS_TTL_SECONDS + 1) is False
    assert len(calls) == 2


def test_a_lagging_tenant_never_reaches_the_dense_leg(vector, monkeypatch):
    """Requirement 5's LIVE wiring: `searcher()` itself must refuse.

    ⛔ This test used to assert `journal_ready(scope) is False` and stop there, so
    it never reached `searcher()` — the six lines that ARE the wiring
    (`record_vector.py`: `if not journal_ready(...): return None`) could be
    deleted with the whole suite still green (#13331 review F5), and every armed
    tenant whose record is short of its journal would then be served from the
    dense leg as `mechanism=pgvector, reason=ok` — the silent-invisibility class
    `#5090` Q2 forbids.
    """
    monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
    monkeypatch.setenv(vector.ARMING_ENV, "1")
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, "/tmp/whatever/events.jsonl")
    tenant = ("org-1", "graph-1")

    asked: list = []

    def gate(scope, *, record_dsn=None, timeout_ms=None):
        asked.append((scope.org_id, scope.graph_id, timeout_ms))
        return False                       # the tenant's record LAGS its journal

    monkeypatch.setattr(vector, "journal_ready", gate)
    assert vector.searcher(carried=tenant, timeout_ms=500) is None, (
        "a tenant whose record does not hold its journal was handed a dense "
        "searcher — the per-tenant condition on the deployment switch is gone")
    # …and the gate was asked about THIS tenant, with the read's own budget, so
    # the coverage read it performs cannot outlive the request (#13331 F3).
    assert asked == [("org-1", "graph-1", 500)]

    # the control: the same tenant, ready, DOES get a searcher.
    monkeypatch.setattr(vector, "journal_ready",
                        lambda scope, *, record_dsn=None, timeout_ms=None: True)
    monkeypatch.setattr(vector, "vector_search",
                        lambda *a, **k: [("p1", 0.1, None)])
    searcher = vector.searcher(carried=tenant, timeout_ms=500)
    assert searcher is not None, "a ready tenant must be served from the record"
    assert searcher("Point", [0.1] * 4, 5) == [("p1", 0.1, None)]


def test_an_unreadable_journal_is_not_ready(vector, single_tenant, monkeypatch, tmp_path):
    """Fail-closed: a check that could not run never reads as ready.

    ⛔ ATTRIBUTABLE TO THE JOURNAL LEG (#13331 review F5/C). The earlier cut of
    this test pointed the declaration at a path that does not exist and asserted
    only that `_measure_readiness` answers `False` — but `EventLog.position()`
    RETURNS 0 for a missing path, so the journal arm never ran and the `False`
    came from the RECORD door (`open_record(None)` raises "no record DSN").
    Reverting the journal arm to fail OPEN left that test green: the property in
    its docstring was unasserted, and the fail-open shape it was supposed to pin
    is exactly review F3/B.

    Two things make it attributable now, and neither can be satisfied by the
    record leg: the RECORD door is a spy that fails the test if it is consulted
    at all (the journal leg must answer first), and the count itself is
    asserted to be UNMEASURED (`None`, never the `0` that reads as "empty" →
    "nothing missing" → ready).
    """
    import tortoise.record_serve as record_serve

    consulted: list = []

    def door(*a, **k):                       # pragma: no cover - must not run
        consulted.append((a, k))
        raise AssertionError(
            "the readiness gate reached the RECORD door for a journal it could "
            "not count — the `False` this test asserts would then be about the "
            "record, not about the journal")

    monkeypatch.setattr(record_serve, "open_record", door)

    missing = tmp_path / "nope" / "events.jsonl"
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(missing))
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")

    # ⛔ THE ROOT OF THE FAIL-OPEN (review F3/B): a path that is not there is
    # NOT a journal of zero records. `position()` answers 0 for it (its own
    # documented contract); the read-only count answers None, so no caller can
    # mistake "could not count" for "counted, and nothing is missing".
    from tortoise.log import EventLog

    assert EventLog(missing).position() == 0
    assert EventLog(missing).position_readonly() is None

    assert vector._measure_readiness(scope, record_dsn=None) is False
    assert consulted == [], "the record door answered a question about the journal"

    # the same answer for an EXISTING path that is not a readable journal: a
    # directory raises out of `position()`'s `a+b` open (the traceback review
    # F3's second half reports), and is `None` here.
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(tmp_path))
    assert vector._measure_readiness(scope, record_dsn=None) is False
    assert consulted == [], "the record door answered a question about the journal"

    # …and for a count that RAISES (a real raise from the reader, which the
    # journal arm must convert into "not ready" rather than propagate).
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(missing))
    monkeypatch.setattr(EventLog, "position_readonly",
                        lambda self: (_ for _ in ()).throw(OSError("EIO")))
    assert vector._measure_readiness(scope, record_dsn=None) is False
    assert consulted == [], "the record door answered a question about the journal"


def test_the_gate_refuses_a_process_global_journal_it_cannot_apply_to_every_tenant(
        vector, monkeypatch, tmp_path, caplog):
    """One path, process-global ⇒ one tenant (#9192, #13331 review F4).

    The declaration names ONE journal. Applying it on a process that serves
    several tenants compares tenant A's journal against tenant B's rows — which
    is not a weaker answer than the scope, it is a WRONG one, and it can read
    `unprojected == 0` for a tenant that is far behind its own journal. So on a
    process that did not declare single tenancy the gate is NOT READY (the graph
    branch answers, #11642 P1) and says why, once per process.
    """
    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}")
    monkeypatch.setenv(vector.RECORD_JOURNAL_ENV, str(journal))
    monkeypatch.delenv("TORTOISE_RECORD_SINGLE_TENANT", raising=False)
    scope = vector._Scope(org_id="org-1", graph_id="graph-1")

    measured: list = []
    monkeypatch.setattr(vector, "_measure_readiness",
                        lambda scope, *, record_dsn=None, timeout_ms=None:
                        measured.append(1) or True)

    assert vector.journal_ready(scope) is False, (
        "a process-global journal was applied to a tenant on a process that "
        "never declared single tenancy — a cross-tenant coverage comparison")
    assert measured == [], "the declaration was applied despite not applying"
    assert any("TORTOISE_RECORD_SINGLE_TENANT" in rec.message
               for rec in caplog.records), (
        "the refusal is SILENT: nothing names the declaration that would make "
        "this tenant measurable")
    caplog.clear()
    assert vector.journal_ready(scope) is False
    assert not any("TORTOISE_RECORD_SINGLE_TENANT" in rec.message
                   for rec in caplog.records)     # once per process

    # the declaration is what unlocks the measurement — nothing else.
    monkeypatch.setenv("TORTOISE_RECORD_SINGLE_TENANT", "1")
    assert vector.journal_ready(scope) is True
    assert measured == [1]


# ── 5. the journal count a READ path is allowed to make ────────────────────


def test_the_read_only_count_takes_no_lock_and_needs_no_write_permission(
        monkeypatch, tmp_path):
    """`position_readonly` is the only count a request path may pay (#13331 F2).

    `EventLog.position()` takes the append lock (a wedged peer parks it for 5 s
    and a timeout stamps the path UNHEALTHY for 30 s, so a READ can suppress a
    concurrent writer's `seq` for half a minute) and opens the journal `a+b`
    (so a read-only mount cannot be counted at all). Both are proved here by
    making each of them FAIL while the read-only count still answers.
    """
    import io

    import tortoise.log as log_module
    from tortoise.log import EventLog

    journal = tmp_path / "events.jsonl"
    journal.write_text("{}\n{}\n{}\n")

    def forbidden(*a, **k):                 # pragma: no cover - must not run
        raise AssertionError(
            "the read-only count paid a side effect `position()` has: the append "
            "lock and the `a+b` write fd are what a request path must not take")

    monkeypatch.setattr(log_module, "_append_lock", forbidden)
    monkeypatch.setattr(io, "FileIO", forbidden)

    assert EventLog(journal).position_readonly() == 3
    with pytest.raises(AssertionError):
        EventLog(journal).position()


def test_the_read_only_count_reports_unmeasurable_as_none_not_zero(tmp_path):
    """The distinction review F3/B is built on: absent is not empty."""
    from tortoise.log import EventLog

    assert EventLog(tmp_path / "nothing.jsonl").position_readonly() is None
    assert EventLog(tmp_path).position_readonly() is None           # a directory
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert EventLog(empty).position_readonly() == 0                 # a real zero


def test_the_unmeasured_journal_leg_is_unanswerable_not_clean():
    """A leg that RAN and could not count the journal is not a clean pass.

    `JournalCoverage(enumerated=None)` is what a caller gets when the count
    failed; the result must say so — `clean` there tells an operator the record
    holds every position on the strength of a check that never ran, and `0` on
    the wire is indistinguishable from a measured zero (#5090 Q2, #13331 F3/B).
    """
    unmeasured = _result(journal=_coverage(2, 3, 1, 2, 2, None))

    assert unmeasured.journal.unprojected is None
    assert unmeasured.verdict() == VERDICT_UNANSWERABLE
    assert exit_code_for(unmeasured) == EXIT_UNAVAILABLE
    assert unmeasured.as_dict()["unprojected_journal"] is None
    line = log_result(unmeasured)
    assert "could NOT be counted" in line
    assert "record_runner.py apply" not in line
