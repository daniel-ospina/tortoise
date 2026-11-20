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
file becomes an export; concurrent writers serialise on a real transaction
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


def test_an_unarmed_deployment_never_asks_and_never_reads(monkeypatch):
    """OFF is today's behaviour byte for byte — no socket, no read, no query.

    `record_live_allocate()` is checked FIRST for that reason: an unarmed
    deployment is the overwhelming majority of installs, and a switch that costs
    a database round trip when it is OFF is a switch nobody leaves off.
    """
    def bomb(*_a, **_kw):
        raise AssertionError(
            "an unarmed deployment opened the record to ask a cutover question")

    monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    monkeypatch.setattr(rw, "_default_connect", bomb)
    monkeypatch.setattr(rw, "record_live_allocate", lambda: False)

    assert rw.record_owns_position(tenant=("o", "g")) is False


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


def test_the_import_failure_arm_appends(monkeypatch):
    """`live_write._record_owns_position` never raises — a broken record module
    must not stop a write that has not happened yet."""
    def boom(*_a, **_kw):
        raise ImportError("record_writer is not installed")

    monkeypatch.setattr(rw, "record_owns_position", boom)
    assert live_write._record_owns_position(durable=True, tenant=None) is False


# ── 3. the seam: the two `seq is None` states stay distinguishable ────────


def test_the_seam_refuses_an_unproven_position_unless_the_append_was_skipped():
    """⛔ THE COLLAPSE THAT WOULD TURN THE FIX INTO AN OUTAGE (#14002).

    `seq is None` has two causes and they need opposite answers:
    `#9307`'s "the append succeeded and could not prove a position" is a
    REFUSAL (the counter would issue a number nothing corroborates), while
    "there is no line, the counter is the only allocator" is the healthy
    cutover. One flag separates them, and this leg pins the ARITHMETIC of the
    distinction without a database: the unproven arm is reached exactly when
    `journal_skipped` is False.
    """
    import inspect

    source = inspect.getsource(rw.write_live_record)
    assert "if seq is None and allocate and not journal_skipped:" in source, (
        "the `position_unverifiable` refusal no longer excludes the "
        "journal-skipped arm — every write on a cut-over graph would be "
        "refused (`write_failed`, counted, WARNed) while the append is "
        "correctly skipped, i.e. a total outage instead of a fix")
    assert "allocate = allocate or record_live_allocate() or journal_skipped" in source, (
        "a skipped append no longer implies allocation — the write would take "
        "the enumerated arm with no ordinal at all")


def test_the_guard_itself_is_untouched():
    """⛔ THE ISSUE FORBIDS FIXING THIS BY WEAKENING THE GUARD.

    The divergence check must still run for any ordinal a caller holds (a direct
    caller, a pre-cutover graph, `backfill_copy_turns`). The fix removes the
    second allocator; it does not relax the comparison.
    """
    import inspect

    source = inspect.getsource(rw.write_live_record)
    assert "if allocate and seq is not None and write.applied:" in source, (
        "the cutover invariant's comparison was changed — the guard is right "
        "and is the only thing that kept the record honest while both "
        "allocators existed (#14002 explicitly forbids weakening it)")
    assert "position_divergence" in source


# ── 4. the rebuild door: the journal is no longer the rebuild source ─────


def test_a_rebuild_is_refused_where_the_record_owns_the_ordinal(monkeypatch,
                                                                capsys):
    """⛔ THE CONSEQUENCE #14002 CREATES, GATED AT THE DOOR THAT WOULD ACT ON IT.

    `tortoise rebuild` is a WIPE + journal-only replay, and
    `STORAGE-ARCHITECTURE.md` §3 makes journal completeness a PRECONDITION of
    `derived = replay(journal)`. On a cut-over graph the file stops being
    appended to, so a rebuild from it would produce a graph as of the arming
    point and **silently drop every later record** — `rebuild_all` prints
    `Done:` either way, and the counts it prints are the journal's own.

    The gate is deliberately broader than per-graph (a `record_cursor` row is
    keyed by `(org_id, graph_id)` and this door has neither): armed deployment +
    a resolvable record DSN ⇒ refuse, with `unset the flag` as the escape. Too
    broad refuses a rebuild that would have been fine; too narrow destroys work.
    """
    from tortoise.__main__ import _journal_rebuild_refusal

    monkeypatch.delenv(rw.RECORD_ALLOCATE_ENV, raising=False)
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    assert _journal_rebuild_refusal() is None, (
        "an un-armed deployment refused a journal rebuild — the journal IS the "
        "rebuild source there")

    monkeypatch.setenv(rw.RECORD_ALLOCATE_ENV, "1")
    refusal = _journal_rebuild_refusal()
    assert refusal is not None and "EXPORT" in refusal, (
        "an ARMED deployment allowed a journal rebuild: the journal is an "
        "export there, so the rebuild would silently drop every record after "
        "the cutover point")
    assert "Nothing was wiped" in refusal

    # A MALFORMED DSN is not an armed record: nothing can allocate, the append
    # stays, and the journal is complete — so the rebuild is allowed.
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "not-a-dsn")
    assert _journal_rebuild_refusal() is None

    # And the CLI turns it into the established `Refused:` / exit-1 contract
    # rather than a traceback or a silent success.
    monkeypatch.setenv(rw.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    from tortoise.__main__ import _cmd_rebuild

    assert _cmd_rebuild(SimpleNamespace(dir=".", db="mem")) == 1
    assert "Refused:" in capsys.readouterr().err


# ── 5. the wiring gate: the deletion class, with its mutations ────────────

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
            ("as None", "            journal_skipped=None)")):
        problems = problems_for(source.replace(old, replacement, 1))
        assert problems, (
            f"the gate certified a door that supplies journal_skipped {label} — "
            "a constant reproduces the two-allocator loss (False: the append is "
            "skipped and the write is refused as `position_unverifiable`; True: "
            "allocation is forced on a graph that is not cut over), which is "
            "exactly the false PASS this check exists to stop")


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
