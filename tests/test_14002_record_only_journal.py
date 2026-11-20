"""#14002 — take the JSONL off the hosted write path.

THE DEFECT. On a graph that has been cut over, the live door asked the record's
counter for the ordinal AND appended the record to the JSONL, so two allocators
had to agree. They cannot agree under concurrency: the journal is ordered by
APPEND (the SDK appends, then calls the door, holding no lock across the two)
while the counter is ordered by ALLOCATION, so two in-flight writes can be
numbered in the opposite order to their lines. The one that allocated first
refuses, the other applies, and the counter is left exactly ONE BEHIND the
journal — and on a cut-over graph that gap never closes (an enumerated replay is
refused there), so every later armed write refuses until an operator re-seeds.
Measured on the hosted lane: `record.ok: false`, `status: degraded`, a
`position_divergence` refusal every few seconds, some writes landing and some
refused.

THE FIX IS NOT IN THE GUARD. The guard is right and it is the only thing that
kept the record honest while both allocators existed; it stays, unchanged, for
every ordinal a caller hands the door. What this change removes is the SECOND
ALLOCATOR: while the record owns the position, the write path must not append to
the JSONL at all. One number space, so a disagreement is not expressible; the
file becomes OPTIONAL (a FROZEN PREFIX on a cut-over graph — not an export:
nothing writes it, `event_id`/`initiated_by`/`projection_version` have no
column); concurrent writers serialise on a real transaction
instead of a file append; and the truncation class of failure disappears because
nothing numbers off the file.

WHAT THIS FILE HOLDS DOWN, and how each half could go quietly wrong:

* the DECISION (`record_writer.record_owns_position`) reads the flag AND the
  graph's cursor row. Half of it is a silent disaster either way: flag alone
  skips the append for a graph whose allocation will refuse (the write lands in
  NEITHER store), row alone skips it for a deployment that never armed;
* the CHOKEPOINT skips the append only on a MEASURED yes, and forwards the
  decision to the seam. Every failure of the measurement answers "append" — the
  pre-#14002 path, guarded — because assuming the record owns it destroys the
  write rather than refusing it;
* the SEAM tells the two `seq is None` states apart. Collapsing them turns the
  fix into a total outage: every write on a cut-over graph comes back
  `position_unverifiable`, counted and WARNed, for ever;
* the WIRING GATE reds if any of that is deleted (see the mutation legs at the
  end) — the class this repo has already shipped once (#12894's door refused
  every write while the gate printed WIRED).

The DB-free legs run in the matrix; the live legs (a real Postgres carrying the
record migrations, `TEST_RECORD_DB_URI`) skip without that DSN and are armed by
the `test-record-postgres` job. The live section is where the issue's ACCEPTANCE
is measured: a sustained concurrent load with ZERO divergence refusals, the
record's positive counter tracking writes 1:1, and the JSONL's size and mtime
unchanged across the window.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from tortoise import live_write
from tortoise import record_writer as rw

_TOOLS = Path(__file__).resolve().parent.parent / "tools"
_HAS_RECORD_DB = os.environ.get("TEST_RECORD_DB_URI")

#: One record that expands to node/identity mutations AND a position row, so a
#: write is never vacuous — the same shape `tests/test_12894_record_cursor_live.py`
#: uses, so the two suites cannot disagree about what a record looks like.
RECORD = {
    "type": "PointAdded",
    "ts": "2026-11-20T00:00:00Z",
    "id": "p-14002",
    "point": {"id": "p-14002", "content": "the record owns the ordinal",
              "pointKind": "claim"},
}


def _record(i: int) -> dict:
    pid = f"p-14002-{i}"
    return {
        "type": "PointAdded",
        "ts": "2026-11-20T00:00:00Z",
        "id": pid,
        "point": {"id": pid, "content": f"claim {i}", "pointKind": "claim"},
    }


class _Log:
    """An `EventLog`-like double that records what was appended to it."""

    durable = True

    def __init__(self, seq: int = 7):
        self.seq = seq
        self.appended: list = []

    def append(self, event):
        self.appended.append(event)
        return self.seq


class _Projection:
    def __init__(self):
        self.applied: list = []

    def apply(self, event):
        self.applied.append(event)


def _outcome(*, applied: bool, refusal: str | None = None):
    return SimpleNamespace(applied=applied, refusal=refusal, outcome="applied")


def _project(*, applied: bool = True, refusal: str | None = None,
             seen: list | None = None, raises: BaseException | None = None):
    """A door-supplied record callable that records HOW it was called."""

    def call(event, seq, *, journal_skipped=False):
        if seen is not None:
            seen.append({"seq": seq, "journal_skipped": journal_skipped})
        if raises is not None:
            raise raises
        return _outcome(applied=applied, refusal=refusal)

    return call


# ── 1. the decision: flag AND row, and never a guess ──────────────────────

class _FakeConn:
    """The smallest thing `record_owns_position` accepts as a connection."""

    def __init__(self):
        self.committed = 0
        self.closed = 0

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed += 1


@pytest.fixture()
def owns_position(monkeypatch):
    """Drive `record_owns_position` with an injected connection and cursor row.

    Returns the door's answer for a given row/mode, WITHOUT a database: the
    question this fixture answers is the DECISION (flag, mode, fail-open), and
    the live leg below proves the read itself against a real Postgres.
    """
    from tortoise import record_allocator as ra

    state = {"row": None, "conn": _FakeConn(), "read": 0}

    def connect(_dsn):
        return state["conn"]

    def read_cursor(_conn, *, org_id, graph_id):
        state["read"] += 1
        return state["row"]

    monkeypatch.setattr(rw, "_default_connect", connect)
    monkeypatch.setattr(ra, "read_cursor", read_cursor)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setenv(rw.RECORD_SINGLE_TENANT_ENV, "1")
    monkeypatch.setenv(rw.RECORD_ORG_ENV, "org-x")
    monkeypatch.setenv(rw.RECORD_GRAPH_ENV, "graph-x")
    return state


def test_an_unarmed_deployment_never_asks_and_never_reads(monkeypatch, caplog):
    """OFF is today's behaviour byte for byte — no socket, no read, no query.

    `record_live_allocate()` is checked FIRST for that reason: an unarmed
    deployment is the overwhelming majority of installs, and a switch that costs
    a database round trip when it is OFF is a switch nobody leaves off.

    ⛔ AND THE BOMB ALONE COULD NOT RED THIS LEG (#14002 review cycle 7,
    FINDING 2 — `#3442` rule 3). It used to raise `AssertionError`, an
    `Exception`: the helper's own fail-open `except Exception` arm CAUGHT it by
    design, warned, and returned `False` — the exact value this test asserted. So
    deleting the flag-first return (R4, the switch's whole cost story) left this
    leg GREEN: the bomb could not escape the arm it was aimed at, and nothing
    looked at whether the read was ATTEMPTED. The falsifier is now the
    ATTEMPT — `asked` — which no fail-open answer can produce, plus the warning
    the swallowed read emits and the throttle stamp it sets. Measured: with
    `record_writer.py`'s `if not record_live_allocate(): return False` deleted,
    `asked == [((), {})]` and the stamp moves, so both new arms red.
    """
    asked: list = []

    def bomb(*args, **kwargs):
        asked.append((args, kwargs))
        raise AssertionError(
            "an unarmed deployment opened the record to ask a cutover question")

    monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", bomb)
    monkeypatch.setattr(rw, "record_live_allocate", lambda: False)
    #: A sentinel, not `0.0`: the fail-open arm's warning is throttled per
    #: process by `OWNS_POSITION_WARN_SECONDS`, and a zeroed stamp would let the
    #: throttle hide the mutant's warning instead of exposing it.
    monkeypatch.setattr(rw, "_owns_position_warned_at", 1234.5)

    with caplog.at_level("WARNING", logger="tortoise.record_writer"):
        assert rw.record_owns_position(tenant=("o", "g")) is False

    assert asked == [], (
        "an unarmed deployment opened the record to ask a cutover question — "
        f"the flag-first return is GONE and every write on every non-cutover "
        f"deployment now pays a round trip for a question the flag answered "
        f"(#14002 R4); connects attempted: {asked}")
    assert rw._owns_position_warned_at == 1234.5, (
        "the answer came from the fail-open arm, not from the flag-first "
        "return — a read was measured (and warned about) on a deployment that "
        "must not touch the record at all")
    assert not any("could not be MEASURED" in rec.getMessage()
                   for rec in caplog.records), (
        "an unarmed deployment emitted the could-not-be-MEASURED warning: it "
        "tried to read the cursor row it must never open")


def test_the_row_must_be_in_active_mode_and_the_read_is_committed(owns_position,
                                                                  monkeypatch):
    """The ROW says cut over; the MODE says positions may be issued. Both.

    A `seeded` graph has not been armed and a `halted` one has been stopped: in
    both, `ALLOCATION_SQL` refuses (`AND mode = 'active'`), so the journal line is
    the only durable copy of the write. Skipping the append there would lose the
    write outright — the allocation refuses AND there is no line behind it.
    """
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")

    for row, expected in (
            ((5, "active"), True),
            ((5, "seeded"), False),
            ((5, "halted"), False),
            (None, False)):
        owns_position["row"] = row
        owns_position["conn"] = _FakeConn()
        assert rw.record_owns_position(tenant=("o", "g")) is expected, (
            f"a cursor row of {row!r} answered "
            f"{not expected} — the row is the FACT and the mode is the "
            "ARMING state, and both decide whether the file append is still "
            "the only durable copy of the write")
        assert owns_position["conn"].committed == 1, (
            "the read transaction was not committed, so the pooled handle is "
            "parked INTRANS holding an AccessShareLock between writes (#9558)")
        assert owns_position["conn"].closed == 1


def test_a_read_that_cannot_be_measured_answers_APPEND_and_says_so(
        owns_position, monkeypatch, caplog):
    """⛔ THE FAIL-OPEN DIRECTION, AND WHY IT IS THAT WAY ROUND (#14002).

    Every unmeasured answer is "the append stays". The worst case is then the
    pre-#14002 path: two allocators, guarded by a refusal. The other direction —
    assuming the record owns it — skips the line for a graph that may not be cut
    over, and a record write that then fails has NO line behind it: the write is
    in neither store, which no refusal can represent and no replay can heal.
    """
    from tortoise import record_allocator as ra

    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    monkeypatch.setattr(rw, "_owns_position_warned_at", 0.0)

    def boom(*_a, **_kw):
        raise RuntimeError("record database is unreachable")

    monkeypatch.setattr(ra, "read_cursor", boom)
    with caplog.at_level("WARNING", logger="tortoise.record_writer"):
        assert rw.record_owns_position(tenant=("o", "g")) is False

    assert any("could not be MEASURED" in rec.getMessage()
               for rec in caplog.records), (
        "the fallback to the append was silent — an operator seeing "
        "position_divergence refusals return cannot tell 'the door refused to "
        "skip' from 'the door could not ask'")


def test_a_connect_failure_is_the_same_answer(owns_position, monkeypatch):
    """No connection is the same unmeasured state as an unreadable row."""
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    monkeypatch.setattr(rw, "_owns_position_warned_at", 0.0)

    def boom(_dsn):
        raise RuntimeError("no route to host")

    monkeypatch.setattr(rw, "_default_connect", boom)
    assert rw.record_owns_position(tenant=("o", "g")) is False


# ── 2. the chokepoint: skip only on a measured yes ────────────────────────


def test_the_chokepoint_does_not_append_when_the_record_owns_the_position(
        monkeypatch):
    """The whole repair, in one assertion: NOTHING is written to the file.

    The counter issues the number, the fold still runs last, and the returned
    outcome says (measurably) that this write has no journal line — which is a
    fact about the write, not a failure.
    """
    monkeypatch.setattr(live_write, "_record_owns_position",
                        lambda **_kw: True)
    log, projection, seen = _Log(), _Projection(), []

    result = live_write.write({"type": "PointAdded"}, log=log,
                              projection=projection,
                              project=_project(seen=seen))

    assert log.appended == [], (
        "the JSONL was appended on a cut-over graph — that is the SECOND "
        "allocator #14002 exists to remove, and it is what makes "
        "`position_divergence` possible under concurrent writers")
    assert result.appended is False
    assert result.seq is None
    assert seen == [{"seq": None, "journal_skipped": True}], (
        "the seam was not told the append was skipped — it will read seq=None "
        "as the #9307 unproven state and refuse EVERY write on this graph")
    assert projection.applied == [{"type": "PointAdded"}], (
        "the cache fold must still run last: derived never leads truth")


def test_a_graph_the_record_does_not_own_appends_exactly_as_before(monkeypatch):
    """OFF / not cut over: one line in the file, the ordinal forwarded, the
    `journal_skipped` flag False. This is the byte-identical arm."""
    monkeypatch.setattr(live_write, "_record_owns_position",
                        lambda **_kw: False)
    log, seen = _Log(seq=41), []

    result = live_write.write({"type": "PointAdded"}, log=log,
                              project=_project(seen=seen))

    assert log.appended == [{"type": "PointAdded"}]
    assert result.appended is True
    assert result.seq == 41
    assert seen == [{"seq": 41, "journal_skipped": False}]


def test_the_decision_is_asked_with_the_write_s_own_tenant(monkeypatch):
    """The graph is chosen by the REQUEST, so the question must carry it.

    A chokepoint that resolved the ambient tenant while the SDK carried another
    pair would skip the append on one graph and allocate on another (#9192's
    mis-scoping class, one layer out).
    """
    asked: list = []
    monkeypatch.setattr(live_write, "_record_owns_position",
                        lambda **kw: asked.append(kw) or False)

    live_write.write({"type": "PointAdded"}, log=_Log(),
                     tenant=("org-a", "graph-a"), project=_project())

    assert asked == [{"durable": True, "tenant": ("org-a", "graph-a")}]


def test_a_refused_record_on_the_record_only_arm_is_not_an_unprojected_line(
        monkeypatch):
    """`appended=False` + `applied=False` = in NEITHER store.

    `unprojected` is the JOURNAL-vs-RECORD quantity, and a skipped append is
    neither a line nor a miss. Reporting it as 1 there would make a LOST write
    read exactly like a recoverable one — the difference #14002's arm makes to
    every consumer of this dataclass.
    """
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: True)

    result = live_write.write({"type": "PointAdded"}, log=_Log(),
                              project=_project(applied=False,
                                               refusal="write_failed"))

    assert result.appended is False and result.applied is False
    assert result.unprojected == 0, (
        "a skipped append was counted as an unprojected LINE — there is no "
        "line, and `unprojected` is what `journal_coverage` counts")
    assert result.refusal == "write_failed"


def test_a_raise_on_the_record_only_arm_does_not_claim_a_position(
        monkeypatch, caplog):
    """The message must not describe a journal position that was never written."""
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: True)

    with caplog.at_level("WARNING", logger="tortoise.live_write"):
        result = live_write.write({"type": "PointAdded"}, log=_Log(),
                                  project=_project(
                                      raises=RuntimeError("record is down")))

    messages = [rec.getMessage() for rec in caplog.records]
    assert any("RECORD-ONLY" in message for message in messages), messages
    assert not any("holds position" in message for message in messages), (
        "the warning named a journal position for a write that never appended")
    assert result.refusal == live_write.PROJECTION_RAISED
    assert result.appended is False
    # ⛔ AND THE QUANTITY, NOT ONLY THE TEXT (#14002 review cycle 8, F2). This leg
    # used to assert the log, the token and `appended` — never `unprojected` — so
    # restoring the pre-#14002 line (`unprojected = 1` on the raise arm) left the
    # whole DB-free suite green (measured). The consequence is not cosmetic:
    # `tools/claim_dense_leg_13732.py` counts this write as `record_unprojected`
    # and its report then asserts "Both are healed by `fold`'s two commands" — a
    # repair that cannot heal a write in neither store, on the one arm
    # `live_write` documents as unrecoverable.
    assert result.unprojected == 0, (
        "a RAISED record-only projection was counted as an unprojected LINE — "
        "there is no line, and a LOST write then reads exactly like a "
        "recoverable one")


def test_a_raise_on_the_append_arm_still_IS_an_unprojected_line(monkeypatch):
    """The contrast that makes the leg above discriminating (#14002 cycle 8, F2).

    Without this, "set it to 0 everywhere" also passes: the append arm's raise is
    a line the record did not take, and `journal_coverage` must keep counting it.
    """
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: False)

    result = live_write.write({"type": "PointAdded"}, log=_Log(),
                              project=_project(
                                  raises=RuntimeError("record is down")))

    assert result.appended is True
    assert result.refusal == live_write.PROJECTION_RAISED
    assert result.unprojected == 1, (
        "the append arm's raise stopped counting as an unprojected line — the "
        "journal holds a position the record does not, which is exactly what "
        "`journal_coverage` reports and `record-reconcile --journal` heals")


def test_the_real_helper_forwards_the_tenant_and_durable_and_the_DECISION(
        monkeypatch):
    """⛔ THE REAL HELPER, NOT A MONKEYPATCH (#14002 review cycle 4, P2).

    Every other chokepoint leg replaces `live_write._record_owns_position`, so
    nothing measured that the helper (i) reaches the record's own predicate,
    (ii) forwards THIS write's `tenant`/`durable`, and (iii) passes its ANSWER on
    instead of a constant. That gap is why a mutant which keeps the call and
    returns False left the whole DB-free suite green. This leg patches the REAL
    `record_writer.record_owns_position` — the seam's predicate, one layer in —
    and drives `live_write.write` through the real helper.
    """
    from tortoise import record_writer

    asked: list = []

    def predicate(*, tenant=None, durable=True):
        # A FAITHFUL double: the record's own predicate refuses a non-durable
        # log, so this one answers `durable` — which is what makes the second
        # half below a real assertion about the outcome and not only about the
        # argument list.
        asked.append((tenant, durable))
        return durable

    monkeypatch.setattr(record_writer, "record_owns_position", predicate)
    monkeypatch.delenv(record_writer.RECORD_ALLOCATE_ENV, raising=False)
    log, seen = _Log(), []

    result = live_write.write({"type": "PointAdded"}, log=log,
                              tenant=("org-z", "graph-z"), durable=True,
                              project=_project(seen=seen))

    assert asked == [(("org-z", "graph-z"), True)], (
        f"the real helper did not ask the record with this write's own "
        f"tenant/durable: {asked}")
    assert log.appended == [], (
        "the helper answered True and the chokepoint appended anyway — the "
        "helper's ANSWER is not reaching the decision")
    assert result.appended is False
    assert seen and seen[0]["journal_skipped"] is True

    # ⛔ AND `durable` IS PART OF THE FORWARDING, NOT A DEFAULT (#14002 review
    # cycle 5, P2). The first cut drove this with `durable=True`, which is ALSO
    # the predicate's own default — so dropping `durable=durable` from the helper
    # passed, and on an armed cut-over graph a SCRATCH buffer (`durable=False`)
    # would then be read as durable, have its append skipped, and be declined by
    # the seam as the healthy `no_durable_journal` decline: the write in NEITHER
    # store, silently, for the M2 capture lane. Driven twice, so the omission and
    # a hard-coded value both red.
    asked.clear()
    scratch_log = _Log()
    scratch_log.durable = False
    live_write.write({"type": "PointAdded"}, log=scratch_log,
                     tenant=("org-z", "graph-z"), project=_project())
    assert asked == [(("org-z", "graph-z"), False)], (
        f"the helper did not forward `durable=False` — a scratch buffer would be "
        f"asked about as if it were a journal: {asked}")
    assert scratch_log.appended == [{"type": "PointAdded"}], (
        "a NON-durable log's append was skipped — nothing could reproduce that "
        "record and the seam declines it, so the write lands in neither store")


# ── 2b. the DOOR's per-call commit signal, on the arm #14002 creates ────


def _refused_record(refusal: str = "circuit_open"):
    """The seam's own REFUSED report — the reachable shape, not a stand-in."""
    return rw.RecordWrite(outcome=rw.RecordWriteOutcome.REFUSED, refusal=refusal)


def _applied_record():
    return rw.RecordWrite(outcome=rw.RecordWriteOutcome.APPLIED, statements=1)


@pytest.fixture()
def sdk_with_journal(tmp_path, monkeypatch):
    """A real SDK with its own embedded graph and its own JSONL journal."""
    from tortoise.sdk import TortoiseSDK

    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "door.db"),
                      event_log_path=str(events / "events.jsonl"))
    try:
        yield sdk, events / "events.jsonl"
    finally:
        sdk.close()


def test_a_refused_record_only_write_is_reported_NOT_landed(
        sdk_with_journal, monkeypatch):
    """⛔ R2, ONE LAYER OUT (#14002 review cycle 7, FINDING 1 — blocking).

    On a cut-over graph the append is skipped, so the RECORD is the only store
    this write could reach. `_emit_event` returned `True` there unconditionally
    ("it did not raise, so it landed"), and two consumers read that `True` as a
    durable copy: `_create_point`'s `WriteNotLandedError` gate could no longer
    fire, and `hosted_api._report_narrative_home` reported the prose as readable
    from a journal that never received the line.

    (1) FAILS PRE-FIX: `_emit_event` answers `True`, so both assertions on the
        returned value red, and the sibling leg below proves `_create_point`
        then WRITES the Point.
    (2) DISCRIMINATING THE OTHER WAY — the second half of this leg: a refused
        record on the APPEND arm must still answer `True`. The journal line is
        the commit point there, and a fix that reds that case would turn every
        record refusal into a lost write on every deployment that has not cut
        over.
    """
    sdk, journal = sdk_with_journal
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: True)
    monkeypatch.setattr(sdk, "_write_record_projection",
                        lambda record, seq, *, journal_skipped=False:
                        _refused_record())

    writes: list = []
    landed = sdk._emit_event(
        "PointAdded", point={"id": "p-refused-14002", "content": "c"},
        write_sink=writes)

    assert writes, "`write_sink` received nothing — the skip fact never left the door"
    assert writes[0].appended is False, (
        "this leg did not reach the record-only arm (the append was not "
        "skipped), so it proves nothing about the cut-over signal")
    assert not journal.exists() or journal.read_text() == "", (
        "the append landed on disk on a graph the helper said is cut over")
    assert landed is False, (
        "a write the record REFUSED was reported as landed: on a cut-over "
        "graph nothing else can hold it (`appended=False, applied=False`), so "
        "`True` here is the door telling its callers a lost write succeeded")

    # …and the APPEND arm keeps the #7951 semantics: the line is the commit point.
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: False)
    assert sdk._emit_event(
        "PointAdded", point={"id": "p-journalled-14002", "content": "c"}) is True, (
        "a refused record on the append arm was reported as NOT landed — the "
        "journal line is committed there, and reporting otherwise rolls back a "
        "write that persisted")


def test_a_skipped_append_the_record_APPLIED_is_landed(sdk_with_journal,
                                                       monkeypatch):
    """The negative control for the leg above: the cut-over arm is not a
    permanent "not landed" either. When the record takes the write, the write
    IS durable (the counter is the allocator) and every pre-existing gate must
    keep passing — otherwise the fix trades a false pass for a total outage."""
    sdk, _journal = sdk_with_journal
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: True)
    monkeypatch.setattr(sdk, "_write_record_projection",
                        lambda record, seq, *, journal_skipped=False:
                        _applied_record())

    assert sdk._emit_event(
        "PointAdded", point={"id": "p-applied-14002", "content": "c"}) is True, (
        "a cut-over write the record APPLIED was reported as not landed — the "
        "record owns the ordinal, so writing it IS the commit")


def test_create_point_refuses_when_the_record_only_arm_lost_the_write(
        sdk_with_journal, monkeypatch):
    """FINDING 1's second consumer, end to end: `add_point` cannot report a
    success for a write that is in NEITHER store.

    Pre-fix there was NO arm on which `_create_point`'s `WriteNotLandedError`
    could fire on a cut-over graph (the append — the only thing that can raise —
    is skipped), so the graph CREATE proceeded and the caller got a Point id
    for an event the record refused.

    (1) FAILS PRE-FIX: no exception, and the Point EXISTS in the cache.
    (2) The Point assertion is the one that matters: a raise alone would be
        satisfied by a gate that fired AFTER the cache write (§17.1's ordering).
    """
    from tortoise.exceptions import WriteNotLandedError

    sdk, _journal = sdk_with_journal
    monkeypatch.setattr(live_write, "_record_owns_position", lambda **_kw: True)
    monkeypatch.setattr(sdk, "_write_record_projection",
                        lambda record, seq, *, journal_skipped=False:
                        _refused_record())

    with pytest.raises(WriteNotLandedError) as ei:
        sdk._create_point("statement", "a claim the record refused")

    assert ei.value.record_committed is False, (
        "the record refused the write, so it is not committed")
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.content CONTAINS $c RETURN count(n)",
        params={"c": "a claim the record refused"}).result_set
    assert int(rows[0][0]) == 0, (
        "the Point was written although nothing holds its record — the cache "
        "led the truth on the one arm where there is no truth store either")


def test_the_narrative_home_is_nowhere_on_a_skipped_append_and_names_the_RECORD(
        caplog):
    """FINDING 1's first consumer (#14022 review cycle 7, blocking).

    `_report_narrative_home` excluded `no_durable_journal` from the `journal`
    branch because a scratch buffer is not a recovery source. #14002 adds a
    SECOND "true but no line" arm — the append was SKIPPED — and it fell into
    the `journalled` branch on the bool, then into the `nowhere` ERROR written
    for the no-journal shape, whose remedy ("arm `TORTOISE_JOURNAL`") is advice
    an operator on a cut-over graph cannot act on: they HAVE a journal, and no
    line will ever be written to it for this record.

    (1) FAILS PRE-FIX: the reporter returned `"journal"` for a refused record on
        the skip arm — the one term that promises a replay heals it.
    (2) DISCRIMINATING: the no-journal arm must still name the journal, or the
        fix has moved the wrong report.
    """
    from tortoise import hosted_api

    caplog.clear()
    home = hosted_api._report_narrative_home(
        session_id="test-14002-narrative", outcome=_refused_record(),
        journalled=False, journal_skipped=True)
    text = " ".join(rec.getMessage() for rec in caplog.records
                    if rec.levelname == "ERROR")
    assert home == "nowhere", (
        f"a refused record on a cut-over graph was reported as {home!r} — the "
        "narrative is in NO store and no replay can heal it")
    assert "record_allocator_cli.py status" in text, (
        "the skip arm's ERROR names no remedy an operator can act on — the "
        f"remedy is the RECORD, not the journal; got: {text!r}")
    assert "TORTOISE_JOURNAL" not in text, (
        "the skip arm told the operator to arm a journal that EXISTS and is "
        "deliberately not being written (#14002) — the fix would be spent on a "
        "no-op")

    # the OTHER `nowhere` shape is unchanged: no journal at all still names it.
    caplog.clear()
    assert hosted_api._report_narrative_home(
        session_id="test-14002-narrative", outcome=None,
        journalled=None) == "nowhere"
    other = " ".join(rec.getMessage() for rec in caplog.records
                     if rec.levelname == "ERROR")
    assert "TORTOISE_JOURNAL" in other, (
        "the no-journal `nowhere` arm lost its remedy — the two shapes have "
        "different fixes and must not be collapsed into one message")


def test_the_import_failure_arm_appends(monkeypatch):
    """`live_write._record_owns_position` never raises — a broken record module
    must not stop a write that has not happened yet."""
    def boom(*_a, **_kw):
        raise ImportError("record_writer is not installed")

    monkeypatch.setattr(rw, "record_owns_position", boom)
    assert live_write._record_owns_position(durable=True, tenant=None) is False


# ── 3. the seam: the two `seq is None` states stay distinguishable ────────


def test_the_seam_REFUSES_an_unproven_position_but_ALLOCATES_a_skipped_one(
        monkeypatch):
    """⛔ THE COLLAPSE THAT WOULD TURN THE FIX INTO AN OUTAGE (#14002), DRIVEN.

    `seq is None` has two causes and they need opposite answers: #9307's "the
    append succeeded and could not prove a position" is a REFUSAL (nothing can
    corroborate the issued number), while "there is no line, the counter is the
    only allocator" is the healthy cutover. One flag separates them.

    ⚠️ MEASURED-REWRITTEN: an earlier cut of this leg asserted two SOURCE
    SUBSTRINGS and called that "the arithmetic" — and `… and not journal_skipped
    and False:` satisfies both, so the pin passed with the arm disabled. It now
    drives the door twice, with the same stubs, and reads the outcome.
    """
    from tortoise import monitoring

    def drive(*, journal_skipped: bool):
        conn = _GuardConn()
        monkeypatch.setattr(rw, "_live_write_verdict",
                            lambda **_kw: (None, ("org", "graph"), None))
        monkeypatch.setattr(rw, "_default_connect", lambda _dsn: conn)
        calls: list = []

        def _write_records(records, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(applied=True, first_seq=7, last_seq=7,
                                   connection=conn, statements=1, events=1,
                                   written_bytes=1, written_rows=1)
        monkeypatch.setattr(rw, "write_records", _write_records)
        write = rw.write_live_record(RECORD, durable=True, seq=None,
                                     tenant=("org", "graph"),
                                     journal_skipped=journal_skipped)
        return write, calls, conn

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    before = dict(monitoring.record_write_failure_counts())

    unproven, unproven_calls, unproven_conn = drive(journal_skipped=False)
    assert unproven is not None and not unproven.applied, (
        "a `seq=None` write that was NOT skipped was allocated — the counter "
        "issued a number nothing corroborates (#9307)")
    assert unproven.refusal == "write_failed"
    assert unproven_calls == [], "the plan ran for a write that must be refused"
    assert unproven_conn.committed == 0

    skipped, skipped_calls, skipped_conn = drive(journal_skipped=True)
    assert skipped is not None and skipped.applied, (
        "a SKIPPED append (the healthy cutover) was refused as "
        "`position_unverifiable` — every write on a cut-over graph would fail")
    assert len(skipped_calls) == 1, "the plan did not run for the skip arm"
    # `allocate` is implied by the skip: a skipped append can only be numbered by
    # the counter, so the plan must be told to allocate (and to start at 1,
    # because `replay_plan` refuses a caller watermark on that arm).
    assert skipped_calls[0]["allocate"] is True
    assert skipped_calls[0]["first_seq"] == 1
    assert skipped_conn.committed == 1

    after = dict(monitoring.record_write_failure_counts())
    assert after.get("write_failed", 0) == before.get("write_failed", 0) + 1, (
        "the unproven refusal was not counted exactly once — the skip arm must "
        "add nothing to `record_write_failures`")


class _GuardConn:
    """A connection double that records the guard's rollback."""

    def __init__(self):
        self.rolled_back = 0
        self.committed = 0
        self.closed = 0

    def rollback(self):
        self.rolled_back += 1

    def commit(self):
        self.committed += 1

    def close(self):
        self.closed += 1


def test_the_divergence_guard_still_REFUSES_a_caller_held_ordinal(
        monkeypatch):
    """⛔ THE ISSUE FORBIDS FIXING THIS BY WEAKENING THE GUARD, AND THIS IS
    BEHAVIOURAL, not a source-substring check (#14002 review cycle 2, P3).

    The check must still fire for any ordinal a caller HOLDS — a direct caller, a
    pre-cutover graph, `backfill_copy_turns` — and must still roll the allocation
    back. MEASURED-REWRITTEN: an earlier cut of this leg asserted two substrings of
    `write_live_record`'s source, and a mutant that turned the guard's own test
    into `if False:` kept BOTH substrings intact — i.e. the pin passed with the
    guard fully disabled. So this drives the door: the allocation is stubbed to
    issue a DIFFERENT number than the ordinal handed in, and the door must answer
    REFUSED, roll back, and never commit.
    """
    from tortoise import monitoring

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    conn = _GuardConn()
    monkeypatch.setattr(rw, "_live_write_verdict",
                        lambda **_kw: (None, ("org", "graph"), None))
    monkeypatch.setattr(rw, "_default_connect", lambda _dsn: conn)

    def _write_records(records, **kwargs):
        # The counter issues 4 while the caller holds the journal's 9.
        return SimpleNamespace(applied=True, first_seq=4, last_seq=4,
                               connection=conn, statements=1, events=1,
                               written_bytes=1, written_rows=1)
    monkeypatch.setattr(rw, "write_records", _write_records)

    before = dict(monitoring.record_write_failure_counts())
    result = rw.write_live_record(RECORD, durable=True, seq=9,
                                  tenant=("org", "graph"), allocate=True)
    after = dict(monitoring.record_write_failure_counts())

    assert result is not None and not result.applied, (
        "the door APPLIED a write whose ordinal (9) disagrees with the number "
        "the allocator issued (4) — the divergence guard is disabled, and the "
        "journal line and its position row would sit at different numbers")
    assert result.refusal == "write_failed"
    assert conn.rolled_back == 1, (
        "the allocation was not rolled back — the position would be BURNED")
    assert conn.committed == 0, "a refused divergent write was committed"
    assert after.get("write_failed", 0) == before.get("write_failed", 0) + 1


def test_the_guard_still_COMPARES_on_the_source():
    """The supplement: the comparison is spelled with `seq is not None`.

    Kept BESIDE the behavioural leg above, not instead of it — this one catches a
    reformat quickly, the behavioural one catches a disabled guard.
    """
    import inspect

    source = inspect.getsource(rw.write_live_record)
    assert "if allocate and seq is not None and write.applied:" in source, (
        "the cutover invariant's comparison was changed — the guard is right "
        "and is the only thing that kept the record honest while both "
        "allocators existed (#14002 explicitly forbids weakening it)")


def test_a_cut_over_reading_is_not_reported_as_an_impossible_journal():
    """⛔ THE P1 OF REVIEW CYCLE 2, AS A LEG: the skip makes `present >
    enumerated` the HEALTHY shape.

    `JournalCoverage.unprojected_unknown_reason` treats "the record holds a
    position its journal never issued" as a contradiction. That was right while
    the append and the counter advanced together — and #14002 makes it the normal
    state of every cut-over graph, because `ALLOCATION_SQL` keeps INSERTing into
    `public.journal_event` while the JSONL stops growing. Read as a
    contradiction, it (i) disarmed the dense leg for ever
    (`record_vector._measure_readiness` returns `False`, with a WARNING claiming
    the journal could not have fed the record) and (ii) made
    `record-reconcile --journal` unable to report clean on a healthy pair — the
    arming step `journal_ready` tells an operator to run.
    """
    from tortoise.record_projection import JournalCoverage

    export = JournalCoverage(present=120, high_seq=120, min_seq=1,
                             within_prefix=0, enumerated=0,
                             journal_is_export=True)
    assert export.unprojected_unknown_reason is None, (
        "a cut-over reading was refused as a contradiction — the dense leg and "
        "`record-reconcile` are then permanently unable to pass on the very "
        "graphs #14002 cut over")
    assert export.unprojected == 0
    assert export.as_dict()["journal_is_export"] is True

    # A truncated and REGROWN journal is still the contradiction it always was:
    # the only difference between the two shapes is whether the record OWNS the
    # ordinal on this graph, which is what the flag carries.
    truncated = JournalCoverage(present=120, high_seq=120, min_seq=1,
                                within_prefix=3, enumerated=3)
    assert truncated.unprojected is None
    assert "cannot hold a position" in (truncated.unprojected_unknown_reason or "")


class _TransactionCursor:
    """A cursor that obeys PostgreSQL's in-failed-transaction rule (``25P02``).

    ⛔ THIS IS THE FALSIFIER FOR THE FAIL-CLOSED PRESENCE READ (#14002 review
    cycle 4, P2). In a real transaction a failed statement leaves it ABORTED
    until ``ROLLBACK`` (or ``ROLLBACK TO SAVEPOINT``), and every subsequent
    statement then fails with ``25P02``. The double enforces exactly that: it is
    the only way a DB-free test can tell "the read answered False" from "the read
    answered False AND poisoned the caller's connection".

    It answers `journal_coverage`'s reads from ``rows`` and records every
    statement it was asked to issue, so the savepoint ORDER is assertable.
    """

    def __init__(self, rows, *, presence_raises: bool = False):
        self._rows = list(rows)
        self._presence_raises = presence_raises
        self.statements: list = []
        self.aborted = False
        self.closed = 0
        self._current = None

    def execute(self, sql, params=None):
        self.statements.append(sql)
        verb = sql.lstrip()
        if self.aborted and not verb.startswith("ROLLBACK TO SAVEPOINT"):
            raise RuntimeError(
                "current transaction is aborted, commands ignored until end of "
                "transaction block (25P02)")
        if verb.startswith("SAVEPOINT"):
            return
        if verb.startswith("ROLLBACK TO SAVEPOINT"):
            # The ONE statement PostgreSQL allows in an aborted block, and the
            # only thing that makes the connection usable again short of a full
            # ROLLBACK (which would also drop the tenant GUC — #9600's silent
            # zero-row shape).
            self.aborted = False
            return
        if verb.startswith("RELEASE SAVEPOINT"):
            return
        if "set_config" in sql:
            self._current = (params[0] if params else "o", False)   # scope read-back
            return
        if "record_cursor" in sql and self._presence_raises:
            self.aborted = True
            raise RuntimeError('relation "public.record_cursor" does not exist (42P01)')
        self._current = self._rows.pop(0) if self._rows else None

    def fetchone(self):
        return self._current

    def close(self):
        self.closed += 1


def test_the_export_read_fails_CLOSED_and_REPAIRS_the_callers_transaction(
        monkeypatch):
    """⛔ A SWALLOWED FAILED STATEMENT USED TO POISON THE CALLER (#14002 cycle 4, P2).

    `_journal_is_export` answers ``False`` on an unreadable record ON PURPOSE (the
    pre-#14002 reading then stands, so a failed read cannot quietly bless a
    rotated journal). But ``False`` was the whole of the recovery: the failed
    statement left the caller's transaction ABORTED, so the NEXT statement on
    that handle failed with ``25P02`` — for a caller that keeps using it
    (`record-reconcile` holds one connection for the whole verb, and a pooled
    handle outlives both). One unreadable row became a spurious failure
    somewhere else entirely, with nothing naming this read.

    Three assertions, and each one has a mutant:

    * the classification is still ``False`` AND the pre-#14002 contradiction
      reading stands (mutant: answer ``True`` — a rotated journal is blessed as
      an export, suppressing the contradiction arm: #13371/#13331 false-arming);
    * the recovery is a ``SAVEPOINT`` / ``ROLLBACK TO SAVEPOINT`` pair (mutant:
      drop the savepoint — the double below is left aborted and the next
      statement reds; a full `conn.rollback()` would instead un-scope the handle);
    * the transaction is USABLE afterwards — asserted by issuing one more
      statement, which is what a caller does and what ``False`` alone fails to
      guarantee.
    """
    from tortoise import record_projection as rp

    # present=9 > enumerated=5: the CONTRADICTION shape, so the fail-closed
    # answer is visible in the verdict and not only in the flag.
    cursor = _TransactionCursor([(9, 9, 1, 9), (None,)], presence_raises=True)
    monkeypatch.setattr(rp, "scope_connection", lambda conn, org_id: cursor)

    cov = rp.journal_coverage(object(), org_id="o", graph_id="g", enumerated=5)

    assert cov.journal_is_export is False, (
        "a failed presence read was read as 'the record owns the ordinal' — the "
        "fail-closed direction the docstring promises is gone, so a rotated "
        "journal is blessed as an export and the dense leg arms on it")
    assert cov.unprojected is None, "the pre-#14002 contradiction reading was lost"
    assert "cannot hold a position" in (cov.unprojected_unknown_reason or "")

    assert any(s.startswith("SAVEPOINT") for s in cursor.statements), (
        f"the presence read was not bracketed in a savepoint: {cursor.statements}")
    assert any(s.startswith("ROLLBACK TO SAVEPOINT") for s in cursor.statements), (
        f"the failed read was swallowed WITHOUT rolling back to its savepoint, "
        f"so the caller's transaction stays aborted: {cursor.statements}")

    # ⛔ THE ASSERTION THE FIX SHIPS WITH: the caller's handle still works. On the
    # pre-fix code this raises "current transaction is aborted … (25P02)".
    cursor.execute("SELECT 1")


def test_the_export_predicate_answers_False_on_every_unmeasured_arm():
    """Every fail-closed answer, asserted where it is taken (#14002 cycle 3, P2).

    The only leg that reached this body used to be a `@pytestmark_live` one, so on
    the DB-free matrix the function returned before any branch ran and the
    fail-closed mutants survived green: `except → True`, `not row → True` and
    `mode → True` each bless a journal the record does NOT own. Constructed
    `JournalCoverage` values cannot pin it — they never call the reader — so the
    reader is driven directly, and the mode arms are read off the same
    `SELECT seq, mode` row the enumerated-replay guard uses.
    """
    from tortoise import record_projection as rp

    class _Cursor:
        def __init__(self, row, *, raises: bool = False):
            self._row = row
            self._raises = raises

        def execute(self, sql, params=None):
            # ONLY the presence read fails: the savepoint bookkeeping around it
            # succeeds, which is the shape a permission error on one table has.
            if self._raises and "record_cursor" in sql:
                raise RuntimeError("permission denied for table record_cursor")

        def fetchone(self):
            return self._row

    assert rp._journal_is_export(_Cursor(None), "o", "g") is False, (
        "a graph with NO counter row was classified as an export — every "
        "pre-cutover tenant would then suppress the contradiction arm")
    assert rp._journal_is_export(_Cursor((4, "seeded")), "o", "g") is False
    assert rp._journal_is_export(_Cursor((4, "halted")), "o", "g") is False, (
        "a seeded/halted cursor was classified as an export — the record is not "
        "allocating this graph's ordinal")
    assert rp._journal_is_export(_Cursor(None, raises=True), "o", "g") is False, (
        "an unreadable record was classified as an export — the one direction "
        "the docstring refuses, because a failed read must not bless a rotated "
        "journal")
    assert rp._journal_is_export(_Cursor((4, "active")), "o", "g") is True


# ── 4. the rebuild door: the journal is no longer the rebuild source ─────


def test_a_rebuild_is_refused_where_the_record_owns_the_ordinal(monkeypatch,
                                                                capsys):
    """⛔ THE CONSEQUENCE #14002 CREATES, GATED AT THE DOOR THAT WOULD ACT ON IT.

    `tortoise rebuild` is a WIPE + journal-only replay, and
    `STORAGE-ARCHITECTURE.md` §3 makes journal completeness a PRECONDITION of
    `derived = replay(journal)`. On a cut-over graph the file stops being
    appended to, so a rebuild from it would produce a graph as of the point the
    append stopped and **silently drop every later record** — `rebuild_all`
    prints `Done:` either way, and the counts it prints are the journal's own.

    The gate is deliberately broader than per-graph (a `record_cursor` row is
    keyed by `(org_id, graph_id)` and this door has neither): a resolvable record
    DSN ⇒ refuse, with an explicit `--journal-is-complete` as the escape. Too
    broad refuses a rebuild that would have been fine; too narrow destroys work.
    """
    from tortoise import __main__ as cli

    # No record configured at all — in the ENV *or* the deployment's `.env`:
    # the journal IS the rebuild source. Both sources are neutralised here
    # because this checkout's `.env` names a record (the dev box is armed), and
    # the point of the leg is the "no record anywhere" arm.
    monkeypatch.delenv(rw.RECORD_DSN_ENV, raising=False)
    monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
    monkeypatch.setattr(cli, "_deployment_names_a_record", lambda: False)
    assert cli._journal_rebuild_refusal() is None, (
        "a deployment with NO record refused a journal rebuild — the journal is "
        "the rebuild source there")

    # ⛔ AND THE DEPLOYMENT'S `.env` COUNTS (#14002 review cycle 4, P2):
    # `record_dsn()` reads only `os.environ` and `tortoise rebuild` loads no
    # `.env`, so a FalkorDB-only maintenance command run from an operator shell
    # saw "no record" and wiped the graph from a frozen journal. Either source
    # naming a record is enough to refuse.
    monkeypatch.setattr(cli, "_deployment_names_a_record", lambda: True)
    assert cli._journal_rebuild_refusal() is not None, (
        "a deployment whose `.env` names a record allowed a journal rebuild from "
        "a shell that does not export the DSN — the fail-open an operator's shell "
        "takes")
    monkeypatch.setattr(cli, "_deployment_names_a_record", lambda: False)
    _journal_rebuild_refusal = cli._journal_rebuild_refusal

    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")

    # ⛔ FAIL-CLOSED ON THE RECORD'S EXISTENCE, NOT ON A WRITER FLAG (#14002
    # review cycle 3, P2). The first cut refused only when
    # `TORTOISE_RECORD_ALLOCATE` was set — which an operator's shell, a cron job
    # and a maintenance one-off do not carry — so the destructive door failed OPEN
    # for exactly the people who run it.
    monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
    refusal = _journal_rebuild_refusal()
    assert refusal is not None and "stops being appended to" in refusal, (
        "a deployment with a RECORD configured allowed a journal rebuild while "
        "the flag was absent — the arm an operator shell takes, where the "
        "rebuild silently drops every record after the cutover point")
    # `.lower()`: the pin is the CLAIM, not a capital letter — a copyedit to
    # "it is not an export yet" must not red, while the pre-fix text (which has no
    # such substring in any case) still does.
    assert "not an export yet" in refusal.lower(), (
        "the refusal claims the file is an export — nothing writes it on a "
        "cut-over graph, so an operator sent looking for one finds nothing")
    # ⛔ AND THE INSTANT IT NAMES IS THE FLAG ROLL, NOT `arm` (PR #14081 review,
    # P2). `record_writer.record_owns_position` bails on `record_live_allocate()`
    # BEFORE it reads the row (`record_writer.py:2849-2850`), and the documented
    # cutover order arms every graph FIRST and rolls the box flag afterwards
    # (`live_write.py:105-109`) — so the arm→roll window is normal and the file
    # STILL GROWS in it. A refusal that dates the frozen prefix at the arming
    # point understates the loss by that whole window, on the one message an
    # operator reads to decide whether a rebuild would drop records.
    assert rw.RECORD_ALLOCATE_ENV in refusal, (
        "the refusal does not name the switch whose roll stops the append — an "
        "operator cannot tell when the file froze, and any single per-graph "
        "instant is EARLIER than the real edge")
    assert "arming point" not in refusal.lower(), (
        "the refusal dates the frozen prefix at the per-graph ARM — the "
        "documented order rolls the box flag afterwards, so a rebuild drops "
        "every record written in that window, not only later ones")
    assert "Nothing was wiped" in refusal
    assert "--journal-is-complete" in refusal, (
        "the refusal does not name its own escape — an operator has no way to "
        "say 'no graph here is cut over'")

    # The escape is the ASSERTION, not a bypass: with it, the rebuild proceeds.
    assert _journal_rebuild_refusal(journal_is_complete=True) is None

    # ⛔ A MALFORMED SHELL DSN IS NOT AN ANSWER (#14002 review cycle 5/6, P2).
    # `record_dsn()` RAISING means the shell could not say — and the deployment's
    # `.env` may still name a usable record, so the question has to be asked
    # there. MEASURED: with this arm reverted to `return None`, the whole file
    # stayed green, so the fail-open the commit says it closed could come back
    # silently while this leg blessed it. Both halves are asserted.
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "not-a-dsn")
    monkeypatch.setattr(cli, "_deployment_names_a_record", lambda: True)
    assert cli._journal_rebuild_refusal() is not None, (
        "a MALFORMED shell DSN was read as 'no record' without asking the "
        "deployment — the destructive door fails open on the one input an "
        "operator shell is least reliable on (`TORTOISE_DB_URI` habits, a "
        "hostless URI), and a cut-over graph would be rebuilt from a frozen "
        "journal")
    # …and with no record in the deployment's `.env` either, the journal IS the
    # only source, so the rebuild is allowed.
    monkeypatch.setattr(cli, "_deployment_names_a_record", lambda: False)
    assert cli._journal_rebuild_refusal() is None

    # And the CLI turns it into the established `Refused:` / exit-1 contract
    # rather than a traceback or a silent success.
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    from tortoise.__main__ import _cmd_rebuild

    assert _cmd_rebuild(SimpleNamespace(dir=".", db="mem")) == 1
    assert "Refused:" in capsys.readouterr().err
    # …and the acknowledgement reaches the door through the CLI surface too.
    assert _journal_rebuild_refusal(journal_is_complete=True) is None


# ── 5. the wiring gate: the deletion class, with its mutations ────────────

#: The freeze POINT, in the prose that states it: the append stops at the BOX
#: flag roll (with the row `active`), never at the per-graph `arm`. Each anchor
#: locates the sentence IN ITS OWN FILE, and the window after it must name the
#: switch and must not re-date the freeze at the cutover.
_FREEZE_POINT_SITES = (
    ("tortoise/live_write.py", "the file is a FROZEN PREFIX"),
    ("tortoise/record_writer.py",
     "the file is a FROZEN PREFIX rather than an export"),
    ("tortoise/__main__.py", "the journal stops being appended to"),
)


def test_every_surface_that_names_the_freeze_point_names_the_flag_roll():
    """⛔ THE INSTANT IN THE VOCABULARY IS PINNED, BECAUSE NOTHING EXECUTES IT.

    The wrong instant survived exactly because these sentences are prose: #14081
    removed "the JSONL is an EXPORT" from four sites and replaced it with "it
    stops at the cutover instant" — while the SAME PR stated the correct
    mechanism twice in `record_projection.py` (`:7819`, `:8074`) and once in the
    evidence record. Both statements were in the tree and the suite was green,
    so a *disclosure* (the reviewer's word) becomes a leg here.

    The mutant is the shipped wording, not a phrase this PR invented: reverting
    any site to `it stops at the cutover instant` (or the refusal to `as of the
    arming point`) reds this leg. `record_owns_position` bails on
    `record_live_allocate()` before it reads the row (`record_writer.py:2849`),
    and the documented order arms every graph first and rolls the flag
    afterwards (`live_write.py:105-109`), so the arm→roll window is normal and
    the file still grows in it.
    """
    root = Path(__file__).resolve().parent.parent
    switch = "TORTOISE_RECORD_ALLOCATE"
    for rel, anchor in _FREEZE_POINT_SITES:
        source = (root / rel).read_text()
        assert source.count(anchor) == 1, (
            f"{rel}: the anchor {anchor!r} matches {source.count(anchor)} times — "
            "this leg must be re-pointed at the sentence that states the freeze")
        start = source.index(anchor)
        window = source[start:start + 420]
        assert switch in window, (
            f"{rel}: the frozen-prefix sentence ({anchor!r}) does not name "
            f"{switch} — the append stops at the BOX flag roll with the row "
            "`active`, not at the per-graph `arm`, so a wrong instant sizes the "
            "loss wrong")
        assert "cutover instant" not in window, (
            f"{rel}: the frozen-prefix sentence still dates the freeze at the "
            "cutover — every graph is armed FIRST and the flag rolled "
            "afterwards, so the file still grows in that window")

@pytest.fixture(scope="module")
def wiring_mod():
    spec = importlib.util.spec_from_file_location(
        "record_wiring_check", _TOOLS / "record_wiring_check.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_real_tree_skips_the_append_behind_the_decision(wiring_mod):
    assert wiring_mod.the_chokepoint_skips_the_append() == [], (
        "the wiring gate reports the chokepoint's cutover skip as unwired")
    assert wiring_mod.live_call_sites_forward_the_journal_skip() == []


_MUTATIONS = {
    # name: (old, new) — each is a way #14002's plumbing has been (or could be)
    # deleted while every OTHER claim of the gate still holds.
    "decision-replaced-by-a-constant": (
        "    journal_skipped = _record_owns_position(durable=durable, tenant=tenant)",
        "    journal_skipped = False"),
    "append-moved-out-of-the-guard": (
        "    seq: int | None\n"
        "    if journal_skipped:  # noqa: SIM108 — see above\n"
        "        seq = None\n"
        "    else:\n"
        "        seq = append() if append is not None else log.append(event)",
        "    seq: int | None = (append() if append is not None\n"
        "                        else log.append(event))"),
    "helper-answers-a-constant": (
        "        return record_writer.record_owns_position(\n"
        "            tenant=tenant, durable=durable)",
        "        return False"),
    "decision-not-forwarded-to-the-seam": (
        "outcome = project(event, seq, journal_skipped=journal_skipped)",
        "outcome = project(event, seq, journal_skipped=False)"),
    # ⛔ THE CYCLE-4 MUTANT: the call is KEPT and the ANSWER is discarded. This
    # passed every claim of the gate ("it reaches the record") and every DB-free
    # leg (they monkeypatch the helper), so the whole fix could be disabled with
    # `record wiring — WIRED` and a green suite.
    "helper-calls-the-record-and-ignores-it": (
        "        return record_writer.record_owns_position(\n"
        "            tenant=tenant, durable=durable)",
        "        record_writer.record_owns_position(\n"
        "            tenant=tenant, durable=durable)\n"
        "        return False"),
}


@pytest.mark.parametrize("name", sorted(_MUTATIONS))
def test_the_wiring_gate_reds_when_the_cutover_skip_is_deleted(
        wiring_mod, tmp_path, name):
    """Each mutant leaves every other claim of the gate intact — that is why a
    gate is needed for this at all: the tree compiles and the suite that runs
    the unarmed path stays green."""
    old, new = _MUTATIONS[name]
    source = (Path(wiring_mod.__file__).resolve().parent.parent
              / "tortoise" / "live_write.py").read_text()
    assert old in source, (
        f"the mutation {name!r} no longer matches the tree — it has drifted and "
        "must be re-pointed at the code it describes")
    mutated = tmp_path / "live_write.py"
    mutated.write_text(source.replace(old, new, 1))
    problems = wiring_mod.the_chokepoint_skips_the_append(mutated)
    assert problems, (
        f"{name}: the wiring gate passed a chokepoint whose cutover skip is "
        "gone — this is the #14002 defect restored behind a green gate")


def test_a_door_that_drops_the_flag_is_refused_by_the_gate(wiring_mod, tmp_path):
    """The other half: a door that keeps the decision and forgets to forward it.

    That shape refuses every write on a cut-over graph (`position_unverifiable`)
    while the append is correctly skipped, which is the #12894 P1 defect one
    layer out — so the gate must read the call site, not only the chokepoint.
    """
    source = (Path(wiring_mod.__file__).resolve().parent.parent
              / "tortoise" / "sdk.py").read_text()
    old = "            journal_skipped=journal_skipped)"
    assert old in source, (
        "the mutation no longer matches the tree — the SDK door's forwarding "
        "line has moved and this leg must be re-pointed at it")
    import ast

    def problems_for(mutated: str) -> list[str]:
        path = tmp_path / "sdk_mutant.py"
        path.write_text(mutated)
        return wiring_mod.journal_skip_forwarding_problems(
            ast.parse(path.read_text()), "tortoise/sdk.py")

    # ⛔ DROPPING IT IS NOT THE ONLY WAY TO LOSE IT (#14002 review, P2). A door
    # that forwards a CONSTANT produces the same loss from the other side:
    # `False` skips the append and the seam refuses the write (neither store);
    # `True` forces allocation on a graph that is not cut over
    # (`cursor_not_active`). Both passed this gate while it certified on the
    # keyword's PRESENCE alone, so all four shapes are asserted.
    for label, replacement in (
            ("dropped", "            )"),
            ("as a constant False", "            journal_skipped=False)"),
            ("as a constant True", "            journal_skipped=True)"),
            ("as None", "            journal_skipped=None)"),
            # ⛔ AND A NAME WITH NO PROVENANCE AT ALL (#14002 review cycle 3,
            # P3; relabelled cycle 9, F2): `_local_constant` is bound NOWHERE in
            # the door, so this case exercises the no-provenance arm — it reds on
            # the undefined name, identically before and after the provenance fix,
            # and the `journal_skipped = True` this case used to inject was dead
            # code (never forwarded). The shape its old label claimed — bind
            # locally, forward THAT name — is the REBOUND-PARAMETER leg below.
            ("via a name bound nowhere",
             "            journal_skipped=_local_constant)")):
        mutated = source.replace(old, replacement, 1)
        problems = problems_for(mutated)
        assert problems, (
            f"the gate certified a door that supplies journal_skipped {label} — "
            "a constant reproduces the two-allocator loss (False: the append is "
            "skipped and the write is refused as `position_unverifiable`; True: "
            "allocation is forced on a graph that is not cut over), which is "
            "exactly the false PASS this check exists to stop")

    # ⛔ AND THE FORM A LANE ACTUALLY WRITES: KEEP THE PARAMETER, REBIND IT
    # (#14002 review cycle 8, F1). The `via a name bound nowhere` case above
    # forwards a name that does not exist, so it exercises the no-provenance arm. The shape that shipped green was the one-assignment-away form the
    # rule's own docstring forbids: leave `journal_skipped=journal_skipped)` in
    # place, inject `journal_skipped = True` above it, and the name is in the
    # SIGNATURE — so the parameter arm accepted it and the provenance check below
    # never ran. Measured on the pre-fix gate: exit 0 / `WIRED` with the whole
    # DB-free suite green, forcing allocation on every write on every graph.
    rebound = source.replace(
        "        log = self._event_log",
        "        journal_skipped = True\n        log = self._event_log",
        1)
    assert rebound != source, (
        "the SDK door's body has moved — the parameter-rebind leg must be "
        "re-pointed at the line it injects above")
    assert problems_for(rebound), (
        "the gate certified a door that REBINDS its own `journal_skipped` "
        "parameter to a constant above the call and forwards it under the same "
        "name — a name this scope ASSIGNS is not the caller's decision, so the "
        "parameter arm of the provenance rule must not accept it (#14002 review "
        "cycle 8, F1)")
    # The contrast that makes the leg above discriminating: the UNMUTATED door
    # must still pass, or a rule that simply refuses every parameter would look
    # green here while breaking both real doors.
    assert not problems_for(source), (
        "the gate now refuses the SDK door's own forwarding shape — the fix "
        "must narrow the parameter arm, not abolish it: "
        f"{problems_for(source)}")


def test_the_cutover_rule_is_ARMED_inside_check(wiring_mod, monkeypatch):
    """⛔ THE ARMING, NOT JUST THE RULE (`#3442` rule 3 — carried from review
    cycle 2/4 as P2 and still unpinned at this head).

    Every leg above drives a rule FUNCTION directly, which is why none of them
    can see the one line that makes the rule run in production:

        position_problems = (… + live_call_sites_forward_the_journal_skip())

    Delete it and this whole file stays green — measured, not argued: the seven
    gate legs pass, and so do `test_9078`'s eleven `position`/`D3`/`wiring`
    legs — while `record_wiring_check.py` still exits 0 and still prints
    `WIRED` (the capstone's subprocess leg asserts exactly that output). So the
    cutover gate — the guard that holds every door to forwarding the decision —
    could be disarmed in production with 100% of the suite green, which is the
    `#12894` false-PASS class this PR exists to close, and the same defect this
    repo fixed once before (`test_7949_capstone_verdict_check.py`: "deleting the
    schema test from `check()` left the whole suite green").

    Behavioural, DB-free: one rule is monkeypatched to report one problem, and
    `check()` must turn it into NOT WIRED. The unpatched run is the control —
    a `check()` that refused everything unconditionally would pass the mutant
    assertion alone.
    """
    # The control: on this tree the gate is WIRED. If it is ever not, that is a
    # real red here rather than a leg that silently proves nothing.
    code, report = wiring_mod.check()
    assert (code, report["verdict"]) == (0, "WIRED"), (
        "the wiring gate is not WIRED on the real tree, so the mutant arm below "
        f"would prove nothing: verdict={report.get('verdict')!r} "
        f"code={code} position_problems={report.get('position_problems')!r}")

    monkeypatch.setattr(wiring_mod, "live_call_sites_forward_the_journal_skip",
                        lambda: ["a door dropped journal_skipped="])
    code, report = wiring_mod.check()
    assert code == 1 and report["verdict"] == "NOT WIRED", (
        "`check()` no longer includes live_call_sites_forward_the_journal_skip "
        "— the gate that holds every door to forwarding the cutover decision is "
        "UNENFORCED in production, and the whole suite is green without it "
        f"(verdict={report.get('verdict')!r} code={code} "
        f"position_problems={report.get('position_problems')!r})")


# ── 5. the live legs: the issue's ACCEPTANCE, on a real Postgres ──────────

#: The DSN gate. A skip here is visible on the `test-record-postgres` job, whose
#: leg gate reds on ANY skip; locally the variable is what makes these run.
pytestmark_live = pytest.mark.skipif(
    not _HAS_RECORD_DB,
    reason="TEST_RECORD_DB_URI not set — no Postgres carrying the record "
           "migrations (the `test-record-postgres` job arms these legs)")


@pytest.fixture(scope="module")
def record_schema():
    """The record schema + the byte meter, applied ONCE for this module.

    The apply ORDER is load-bearing (the byte meter's EXECUTE grant lives in a
    step that must run AFTER the metering migration creates the function), and
    re-applying it per test takes an ACCESS EXCLUSIVE lock on
    `public.record_cursor` — which is the #12399 deadlock when a pooled handle
    from the previous test still holds a share lock. So: once per module, and
    `_release_pooled_handles` between tests.
    """
    pytest.importorskip("psycopg2")
    from tests.test_9078_record_written_by_production_path import (
        _ensure_metering_schema,
        _ensure_record_schema,
        _record_db,
    )

    conn = _record_db()
    _ensure_record_schema(conn)      # the base shape the metering files depend on
    _ensure_metering_schema(conn)    # …14… — the meter's function comes into being
    _ensure_record_schema(conn)      # …16…+`…02…` — the GRANT step, re-applied
    try:
        yield conn
    finally:
        with contextlib.suppress(Exception):
            conn.close()


@pytest.fixture(autouse=True)
def _release_pooled_handles():
    """Hand back every idle record connection a test's writes opened (#9558).

    Without this the NEXT test's migration re-apply deadlocks against a parked
    handle — measured in the sibling suite, and the reason
    `record_writer._release_pooled_connections()` is documented as "the TEST
    suite's isolation seam".
    """
    yield
    rw._release_pooled_connections()


@contextlib.contextmanager
def _graph(monkeypatch, record_schema, *, mode: str | None = "active"):
    """A fresh tenant, optionally SEEDED and ARMED, with the door's DSN set.

    ``mode``:
      * ``"active"`` — seeded and armed: the record owns the ordinal.
      * ``"seeded"`` — seeded and NOT armed (the state a `reopen` leaves too): the
        allocator refuses, so the journal line is the only durable copy.
      * ``None`` — no `record_cursor` row at all: the pre-cutover graph, i.e.
        every embedded and self-host deployment.
    """
    psycopg2 = pytest.importorskip("psycopg2")
    from tests.test_9078_record_written_by_production_path import _app_role_dsn
    from tortoise import record_allocator as ra

    token = f"{os.getpid()}-{uuid.uuid4().hex[:10]}"
    org, graph = f"org-14002-{token}", f"graph-14002-{token}"
    with record_schema.cursor() as cur:
        cur.execute("INSERT INTO public.organizations (id, name) VALUES (%s, %s)",
                    (org, org))
        cur.execute("INSERT INTO public.graphs (id, org_id, name) VALUES (%s, %s, %s)",
                    (graph, org, graph))
    record_schema.commit()

    if mode is not None:
        app = psycopg2.connect(_app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
        app.autocommit = False
        try:
            ra.seed(app, org_id=org, graph_id=graph, enumerated_floor=0)
            if mode == "active":
                ra.arm(app, org_id=org, graph_id=graph)
            app.commit()
        finally:
            app.rollback()
            app.close()

    monkeypatch.setenv(rw.RECORD_DSN_ENV,
                       _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    try:
        yield SimpleNamespace(org=org, graph=graph, admin=record_schema)
    finally:
        rw._release_pooled_connections()


def _counter_and_rows(conn, org, graph):
    with conn.cursor() as cur:
        cur.execute("SELECT seq, mode FROM public.record_cursor "
                    "WHERE org_id = %s AND graph_id = %s", (org, graph))
        counter = cur.fetchone()
        cur.execute("SELECT seq FROM public.journal_event WHERE org_id = %s "
                    "AND graph_id = %s ORDER BY seq", (org, graph))
        rows = [row[0] for row in cur.fetchall()]
    return counter, rows


def _journal(path: Path):
    from tortoise.log import EventLog

    return EventLog(path)


@pytestmark_live
def test_the_acceptance_zero_divergence_under_concurrent_writers(
        monkeypatch, record_schema, tmp_path):
    """⛔ THE ISSUE'S ACCEPTANCE, MEASURED (window and write count stated).

    Window: one sustained burst of 12 concurrent writer threads x 10 writes each
    = **120 writes**, all in flight together, on one cut-over graph with
    `TORTOISE_RECORD_ALLOCATE=1`. This is the load that produced a
    `position_divergence` every few seconds in production (20+ agents writing
    continuously: two allocators CANNOT agree under concurrency, so the refusals
    scale with the writer count).

    Asserted over that window:
      * ZERO `position_divergence` refusals, and zero record-write failures at
        all (divergence arrives as `write_failed`, so a zero here is part of
        "zero divergence", not a separate claim);
      * every write APPLIED, at positions 1..N with no gap and no repeat — one
        number space, issued by the counter;
      * `record_writes_applied` advanced by EXACTLY 120 (the 1:1 requirement);
      * the JSONL's SIZE and MTIME are unchanged: nothing numbers off the file,
        so it does not grow.

    The falsifier for the last two is
    `test_the_falsifier_an_appending_cut_over_graph_wedges_and_grows_the_file`:
    with the skip disabled the same shape reds.
    """
    from tortoise import monitoring

    threads, per_thread = 12, 10
    total = threads * per_thread
    journal = tmp_path / "events.jsonl"
    journal.write_text("")           # a real (empty) journal: nothing may append
    before_stat = journal.stat()
    applied_before = monitoring.record_write_applied_count()
    failures_before = dict(monitoring.record_write_failure_counts())
    started = threading.Barrier(threads)
    results: list = []
    errors: list = []

    with _graph(monkeypatch, record_schema) as live:
        log = _journal(journal)

        def writer(index: int) -> None:
            try:
                started.wait(timeout=60)
                for i in range(per_thread):
                    results.append(live_write.write(
                        _record(index * per_thread + i), log=log,
                        tenant=(live.org, live.graph)))
            except BaseException as exc:
                errors.append(exc)

        workers = [threading.Thread(target=writer, args=(n,), daemon=True)
                   for n in range(threads)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=600)
        stuck = [worker for worker in workers if worker.is_alive()]
        counter, rows = _counter_and_rows(live.admin, live.org, live.graph)

    applied_after = monitoring.record_write_applied_count()
    failures_after = dict(monitoring.record_write_failure_counts())

    assert not stuck, "a writer thread did not finish inside 10 minutes"
    assert not errors, f"a writer raised: {errors[0]!r}"
    assert len(results) == total, (
        f"{len(results)} of {total} writes returned — a thread lost its results")

    refused = [r for r in results if not r.applied]
    assert not refused, (
        f"{len(refused)} of {total} writes were REFUSED on a cut-over graph — "
        f"first: refusal={refused[0].refusal!r} record={refused[0].record!r}. "
        "The two-allocator wedge is back: the journal append and the counter "
        "cannot agree under concurrency, and on a cut-over graph the gap never "
        "closes on its own")

    new_failures = {reason: count - failures_before.get(reason, 0)
                    for reason, count in failures_after.items()
                    if count - failures_before.get(reason, 0) > 0}
    assert new_failures == {}, (
        f"record-write failures were counted over the window: {new_failures}")

    assert applied_after - applied_before == total, (
        f"record_writes_applied advanced by {applied_after - applied_before} "
        f"for {total} writes — the positive counter must track writes 1:1 or "
        "it cannot evidence the cutover")

    # ⛔ THE ISSUE'S SECOND ACCEPTANCE CRITERION, ASSERTED THROUGH THE VERDICT
    # THAT PRODUCES IT (`record.ok: true`, `degraded: false`) rather than
    # inferred from a zero failure count. `degraded` in `/health` is
    # `monitoring.record_refusing()`'s answer — a refusal inside the bounded
    # window — so asserting it here is the product-health half of the acceptance
    # and not a re-statement of the counter: `record_write_failures` alone says
    # nothing about the WINDOW the verdict reads.
    assert monitoring.record_refusing() is None, (
        "the record still reads as REFUSING after a clean window — `/health` "
        "would answer `degraded: true` while the counters above are clean")

    assert counter is not None and counter[1] == "active"
    assert rows == list(range(1, total + 1)), (
        f"the record's positions are not 1..{total}: the counter issued gaps or "
        f"repeats (first 5: {rows[:5]}, last 3: {rows[-3:]})")

    after_stat = journal.stat()
    assert (after_stat.st_size, after_stat.st_mtime) == (
        before_stat.st_size, before_stat.st_mtime), (
        f"the JSONL was WRITTEN on a cut-over graph "
        f"({before_stat.st_size}->{after_stat.st_size} bytes, mtime "
        f"{before_stat.st_mtime}->{after_stat.st_mtime}) — the file must stop "
        "growing while the record owns the ordinal (#14002)")

    assert all(result.appended is False for result in results), (
        "some outcomes claim a journal append happened on a cut-over graph")


@pytestmark_live
def test_the_falsifier_an_appending_cut_over_graph_wedges_and_grows_the_file(
        monkeypatch, record_schema, tmp_path):
    """⛔ THE FALSIFIER THE ISSUE ASKS FOR, DRIVEN DETERMINISTICALLY.

    "An armed+cut-over graph that still appends to the file must fail the test."
    This leg restores exactly that — the skip decision is forced OFF, i.e. the
    pre-#14002 behaviour — and drives the SAME production write path.

    The interleaving is chosen rather than threaded, because a threaded version
    would be a flake lottery and the wedge is not probabilistic once the order is
    fixed: write A appends and allocates in order; then the journal advances by
    one WITHOUT the record (a refused write, a truncated-and-regrown file — the
    states the guard's own docstring names); then write B appends and allocates
    ONE BEHIND. Two facts make the acceptance's assertions discriminating: the
    journal GROWS, and the door REFUSES.
    """
    journal = tmp_path / "events.jsonl"
    journal.write_text("")
    log = _journal(journal)

    with _graph(monkeypatch, record_schema) as live:
        monkeypatch.setattr(live_write, "_record_owns_position",
                            lambda **_kw: False)
        first = live_write.write(_record(0), log=log,
                                 tenant=(live.org, live.graph))
        # The gap: the journal advances, the counter does not.
        log.append(_record(1))
        second = live_write.write(_record(2), log=log,
                                  tenant=(live.org, live.graph))
        counter, rows = _counter_and_rows(live.admin, live.org, live.graph)

    assert first.applied, f"the in-order write was refused: {first!r}"
    assert first.appended is True
    assert journal.stat().st_size > 0, (
        "the file did not grow with the skip disabled — then the acceptance's "
        "'stops growing' assertion would pass for the wrong reason")
    assert not second.applied, (
        "an out-of-order append+allocate pair was ACCEPTED — this is the "
        "divergence the guard must refuse, and it is the load that produced "
        "`position_divergence` every few seconds on the hosted lane")
    assert rows == [1], (
        f"the record holds {rows!r}: the refused write's row must be absent — "
        "its journal line consumed a position no replay can repair on a "
        "cut-over graph")
    assert counter is not None and counter[0] == 1, (
        "the refused write moved the counter — the rollback is the half that "
        "keeps the position from being burned")


@pytestmark_live
def test_the_coverage_reading_knows_the_journal_is_an_export_here(
        monkeypatch, record_schema, tmp_path):
    """⛔ THE REVIEW-CYCLE-2 P1, EXECUTED AGAINST A REAL RECORD (#14002).

    After a handful of record-only writes the record holds positions its journal
    never enumerated (the file was never appended). The reading must say so —
    `journal_is_export` — and must NOT report the contradiction, because
    `record_vector._measure_readiness` reads a `None` `unprojected` as "this
    journal cannot have fed this record" and keeps the GRAPH branch for ever,
    and `record-reconcile --journal` cannot report clean. Both are the surfaces a
    hosted cutover exists to serve.

    The contrast leg is the same reading on a graph the record does NOT own: the
    contradiction arm is still armed there (`tests/test_14002…::
    test_a_cut_over_reading_is_not_reported_as_an_impossible_journal` covers it
    DB-free).
    """
    from tortoise.record_projection import journal_coverage

    journal = tmp_path / "events.jsonl"
    journal.write_text("")
    log = _journal(journal)

    with _graph(monkeypatch, record_schema) as live:
        for i in range(5):
            live_write.write(_record(i), log=log, tenant=(live.org, live.graph))
        # A REAL, SCOPED READER, the way `record_vector._measure_readiness` asks.
        psycopg2 = pytest.importorskip("psycopg2")
        from tests.test_9078_record_written_by_production_path import _app_role_dsn

        reader = psycopg2.connect(
            _app_role_dsn(os.environ["TEST_RECORD_DB_URI"]))
        try:
            cov = journal_coverage(reader, org_id=live.org,
                                   graph_id=live.graph,
                                   enumerated=log.position_readonly(),
                                   want_gap=False)
        finally:
            reader.rollback()
            reader.close()

    assert journal.stat().st_size == 0, "the append happened on a cut-over graph"
    assert cov.present == 5 and cov.enumerated == 0, (
        f"the reading is not the cut-over shape: present={cov.present} "
        f"enumerated={cov.enumerated}")
    assert cov.journal_is_export is True, (
        "the coverage reading did not recognise that this graph's record OWNS "
        "the ordinal, so `present > enumerated` will be read as a contradiction")
    assert cov.unprojected_unknown_reason is None and cov.unprojected == 0, (
        f"a healthy cut-over pair read as unmeasurable "
        f"({cov.unprojected_unknown_reason!r}) — the dense leg would disarm and "
        "`record-reconcile` could never pass")


@pytestmark_live
def test_a_non_cut_over_graph_keeps_todays_behaviour(monkeypatch, record_schema,
                                                     tmp_path):
    """The un-armed / non-cut-over paths are unchanged — embedded, self-host.

    A graph with NO `record_cursor` row and the flag unset is every deployment
    that is not the hosted cutover: the append happens, the append's ordinal is
    the record's position, and the JSONL grows exactly as it did before #14002.
    """
    journal = tmp_path / "events.jsonl"
    journal.write_text("")
    log = _journal(journal)

    with _graph(monkeypatch, record_schema, mode=None) as live:
        monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
        result = live_write.write(_record(0), log=log,
                                  tenant=(live.org, live.graph))
        _counter, rows = _counter_and_rows(live.admin, live.org, live.graph)

    assert result.appended is True and result.seq == 1
    assert journal.stat().st_size > 0, (
        "the un-armed path stopped writing the journal — a change to "
        "embedded/self-host behaviour, which #14002 forbids")
    assert rows == [1], "the enumerated arm must still write the append's number"


@pytestmark_live
def test_an_unarmed_cursor_still_appends_because_the_line_is_the_only_copy(
        monkeypatch, record_schema, tmp_path):
    """⛔ THE ASYMMETRY, PINNED. A `seeded` cursor row refuses every allocation.

    If the append were skipped there too, those writes would be in NEITHER store
    — refused by the counter and unlined by the door. So the skip is keyed on
    `active` (the mode `ALLOCATION_SQL` requires), and the pre-existing refusal
    (`cursor_not_active`, remedy `arm`/`reopen`) keeps its journal line as the
    durable copy.
    """
    journal = tmp_path / "events.jsonl"
    journal.write_text("")
    log = _journal(journal)

    with _graph(monkeypatch, record_schema, mode="seeded") as live:
        result = live_write.write(_record(0), log=log,
                                  tenant=(live.org, live.graph))
        _counter, rows = _counter_and_rows(live.admin, live.org, live.graph)

    assert result.appended is True, (
        "the append was skipped on a graph whose allocator refuses — the write "
        "reached neither store")
    assert journal.stat().st_size > 0
    assert not result.applied and result.refusal == "write_failed"
    assert rows == [], "an un-armed cursor issued a position"
