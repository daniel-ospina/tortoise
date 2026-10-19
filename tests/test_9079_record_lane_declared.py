"""#9079 §17.1 — the writers that reach `proj.g` declare themselves: the RECORD
store and the two REPLAY engines.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 (the §2.0 owner ruling) splits
the store: the journal is the system of record, the graph is a derived cache, so
"every write goes to the truth store first, and the cache is projected from it".
`#7951` enforced that at the fold and at the public raw-query lane; `#9079`
closes the third door — the projection's own write handle, `proj.g`
(`_GuardedGraph`).

Two writer classes reached that handle with NO declaration, and this suite pins
both. Neither is a violation of §17.1; both are writers the door will refuse,
and refusing them is a REGRESSION, not enforcement:

1. **The event store is the RECORD** (`tortoise/event_store.py`). Its
   `:GraphEvent` nodes ARE the durable event log (`read_after` / `poll_events`
   serve them) and `:GraphEventMeta` is their seq allocator, so refusing its
   append enforces a rule ABOUT the record against the record itself. It would
   also fail SILENTLY: `tortoise/sdk.py::_emit_event` calls this store inside
   `except Exception: _logger.warning("event emission failed … continuing")`, so
   a refusal here stops every graph event with only a warning line.
2. **The replay engines** (`FalkorProjection.rebuild_all`,
   `.rebuild_from_record`). Their sibling `rebuild` has opened
   `_replay_write_scope()` since `#7951` (`with _replay_write_scope():
   self._replay_log(…)`); these two issued the wipe, the folds, the index DDL
   and the record projection straight at the handle with no scope at all — so
   closing the door would refuse every journal and record rebuild.

What this suite pins, and why each assertion is not prose:

* every MUTATING statement the record store issues observes a named lane AT THE
  HANDLE — recorded from inside the handle, so a caller that merely succeeds
  today cannot pass;
* an ARMED third door (the guard's own predicate, reproduced verbatim) does not
  refuse the record and does not refuse either replay engine — while it still
  refuses an undeclared `:Point` write through the same handle, so a lane cannot
  be cited as a reason to widen the guard (the `#9068` shape);
* the class the record lane claims is really capture-and-restore, not replay: a
  rebuild carries the `:GraphEventMeta` counter forward and does NOT replay the
  `:GraphEvent` rows (#4664) — so the claim is "the record's home survives a
  wipe by being carried", never "the rows are re-derived from the journal".

It does not arm `#9124`'s guard (that child is not on `main`). `_GuardProbe`
reproduces the guard's PREDICATE — journal-backed + no declaration in flight + a
mutating statement ⇒ refused — the same way
`tests/test_9079_event_writers_journal.py` does, so "these writers will not be
refused" is executable now instead of blocked on a sibling, and the two cannot
disagree about what a declaration is.

MUTATION for the load-bearing tests:

* drop `_record_write` in `tortoise/event_store.py` (call `g.query` directly
  again) → the lane test fails on an observed `None` lane and the armed test
  fails on `DirectCacheWriteRefused` from `CREATE (e:GraphEvent …)`;
* drop either `with _replay_write_scope():` wrapper in `tortoise/projection/
  __init__.py` (`rebuild_all` / `rebuild_from_record`) → that engine's armed
  test fails on the wipe (`MATCH (n) DETACH DELETE n`);
* the rebuild-semantics test still passes under all three mutations — it pins
  what the declarations CLAIM, so the claims cannot drift.
"""
from __future__ import annotations

import pytest

from tortoise.projection import (
    _CACHE_WRITE_AUTHORIZED,
    DirectCacheWriteRefused,
    _GuardedGraph,
    _statement_writes,
)

#: The declared lane name, pinned as a literal: a lane that renames itself
#: silently is a lane a future reader cannot find from the refusal message.
RECORD_LANE = "event-store.record"

#: Index/constraint statements. `FalkorProjection.__init__` and this store's
#: `ensure_event_schema` both issue `CREATE INDEX …`. DDL changes no node, so
#: it is not a §17.1 cache write; it is still DECLARED here (schema is not a
#: journal projection either) — the exclusion is only from the node-write
#: assertion, so a future undeclared index statement is not silently blessed.
_DDL_PREFIXES = (
    "CREATE INDEX", "CREATE CONSTRAINT", "GRAPH.CONSTRAINT",
    "DROP INDEX", "DROP CONSTRAINT",
)


def _is_ddl(statement: str) -> bool:
    return statement.upper().startswith(_DDL_PREFIXES)


# ── fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def sdk_with_journal(tmp_path):
    """A real SDK with a configured JSONL journal, so §17.1's guard is ARMED.

    Same fixture shape as the other #9079 suites: the explicit `db_path` makes
    the projection embedded when no URI is configured, and a URI-set session is
    redirected to its server. `event_log_path` is what arms the door.
    """
    from tortoise.sdk import TortoiseSDK

    journal = tmp_path / "journal.jsonl"
    sdk = TortoiseSDK(str(tmp_path / "record_lane.db"),
                      event_log_path=str(journal))
    sdk._journal_path_for_test = journal  # convenience for the rebuild test
    try:
        yield sdk
    finally:
        sdk.close()


class _GuardProbe:
    """`#9124`'s predicate, reproduced on `_GuardedGraph.query`.

    Records `(statement, lane-at-the-write, is-write)` for every statement that
    reaches the handle, so "this write was declared" is OBSERVED rather than
    inferred from the source, and refuses exactly what the real guard refuses
    when `armed=True`.
    """

    def __init__(self, *, armed: bool) -> None:
        self.armed = armed
        self.seen: list[tuple[str, str | None, bool]] = []
        self.refused: list[str] = []

    def install(self, monkeypatch) -> _GuardProbe:
        original = _GuardedGraph.query
        probe = self

        def instrumented(self, cypher, params=None, timeout=None):
            statement = " ".join((cypher or "").split())
            writes = _statement_writes(statement)
            lane = _CACHE_WRITE_AUTHORIZED.get()
            probe.seen.append((statement, lane, writes))
            if (probe.armed
                    and getattr(self._proj, "_journal_backed", False)
                    and lane is None and writes):
                probe.refused.append(statement)
                raise DirectCacheWriteRefused(
                    "§17.1 third door (test probe of #9124's guard): "
                    f"undeclared cache write through `proj.g`: {statement[:120]}")
            return original(self, cypher, params, timeout)

        monkeypatch.setattr(_GuardedGraph, "query", instrumented)
        return self

    def record_statements(self) -> list[tuple[str, str | None]]:
        """Every mutating statement naming the event store, with its lane."""
        return [(s, lane) for s, lane, w in self.seen
                if w and "GraphEvent" in s]


def _exercise_the_store(sdk) -> None:
    """Drive every mutating path in `event_store` through the public surface.

    create_point reaches `ensure_event_schema` (DDL) → `next_seq` (MERGE
    `:GraphEventMeta`) → `append_event` (CREATE `:GraphEvent`); the purge path
    reaches both DELETEs and `_refresh_first_seq`; the watermark path reaches
    the monotone MERGE.
    """
    from tortoise.event_store import (
        capture_watermark,
        purge_expired,
        purge_overflow,
        reestablish_watermark,
    )

    proj = sdk._get_proj()
    sdk.create_point("fact", "an event the record lane must be able to store")
    sdk.create_point("fact", "a second event, so the seq allocator bumps twice")
    purge_expired(proj, retention_days=0)
    purge_overflow(proj, max_events=0)
    reestablish_watermark(proj, capture_watermark(proj).get("last_seq"))


# ── the declaration ───────────────────────────────────────────────────────


def test_every_event_store_write_declares_the_record_lane(sdk_with_journal,
                                                          monkeypatch):
    """RED before this lane: every store write reached the handle with NO lane.

    `_CACHE_WRITE_AUTHORIZED` was `None` at each of them, which is exactly what
    `#9079`'s handle guard reads as "refuse", so closing the third door would
    have stopped the durable event store on the product's hottest write path.
    """
    probe = _GuardProbe(armed=False).install(monkeypatch)
    _exercise_the_store(sdk_with_journal)

    observed = probe.record_statements()
    assert observed, (
        "no event-store statement was observed at the handle; the exercise, "
        f"not the declaration, is what broke. statements seen: "
        f"{[s[:60] for s, _l, _w in probe.seen][:12]}")
    undeclared = [(s, lane) for s, lane in observed if lane != RECORD_LANE]
    assert not undeclared, (
        "every mutating event-store statement must run inside its declared "
        f"lane {RECORD_LANE!r}; observed undeclared: "
        + " | ".join(f"{s[:100]} (lane={lane!r})" for s, lane in undeclared))

    # NON-VACUOUS: the assertion above is only meaningful if the classes it
    # claims to cover were actually reached. Each of these is a distinct
    # mutating shape, and each must appear.
    classes = {
        "allocator": lambda s: "MERGE (m:GraphEventMeta)" in s,
        "append": lambda s: "CREATE (e:GraphEvent" in s,
        "purge": lambda s: "DETACH DELETE" in s,
        "schema": lambda s: _is_ddl(s) and "GraphEvent" in s,
    }
    missing = [name for name, match in classes.items()
               if not any(match(s) for s, _lane in observed)]
    assert not missing, (
        f"the exercise did not reach {missing} — the coverage claim in this "
        "test would be vacuous. Statements observed: "
        + " | ".join(s[:80] for s, _lane in observed))


def test_an_armed_guard_does_not_refuse_the_record(sdk_with_journal,
                                                   monkeypatch):
    """The indicator that matters for #9079: the SDK's writes still succeed.

    The guard's predicate is armed verbatim over the handle. The declaration is
    what keeps a create from dying on the record's own write — and a create that
    died here would be invisible, because `_emit_event` swallows the exception.
    """
    from tortoise.event_store import read_after

    probe = _GuardProbe(armed=True).install(monkeypatch)
    point = sdk_with_journal.create_point(
        "fact", "a create with the third door armed")

    assert point["id"]
    assert probe.refused == [], (
        "the event store did not declare itself, so arming the third door "
        "refuses the record: "
        + " | ".join(s[:120] for s in probe.refused))

    proj = sdk_with_journal._get_proj()
    events = read_after(proj, 0)
    assert events, (
        "the armed handle left the durable event store EMPTY — this is the "
        "silent-loss shape `_emit_event`'s `except Exception: logger.warning` "
        "produces when a refusal is swallowed (#7951's lesson, one lane over)")
    assert any(e.get("type") == "PointAdded" for e in events), (
        f"the create's own event is missing from the store; observed "
        f"types={[e.get('type') for e in events]}")


def test_the_declaration_is_not_a_global_bypass(sdk_with_journal, monkeypatch):
    """A declared lane is a lane, not an open handle: an undeclared writer is
    still refused by the very same armed guard.

    Without this, `event-store.record` could be cited as a reason to widen the
    guard ("some writers do not journal, so do not close the door"), which is
    the licence `#9068` records as the failure shape to avoid.
    """
    probe = _GuardProbe(armed=True).install(monkeypatch)
    proj = sdk_with_journal._get_proj()

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'undeclared-demo'})")

    assert probe.refused, "the armed probe did not record the refusal"
    assert "undeclared-demo" in probe.refused[0]

    # A READ through the same handle is untouched by the door.
    rows = proj.g.query(
        "MATCH (n:Point {id:'undeclared-demo'}) RETURN count(n)").result_set
    assert int(rows[0][0]) == 0, (
        "the refused write must have landed NOTHING — the refusal is pre-write")


# ── the two replay engines reach the same door ────────────────────────────


def test_the_journal_replay_runs_inside_the_replay_scope(sdk_with_journal,
                                                         monkeypatch, tmp_path):
    """RED before this lane: `rebuild_all` issued its wipe through the handle
    with no scope, while its sibling `rebuild` has opened one since #7951.

    An armed door therefore refused the REPLAY itself — every journal rebuild,
    which is the recovery path a lost cache depends on.
    """
    sdk = sdk_with_journal
    # The point is created BEFORE the probe is armed on purpose: this test is
    # about the REPLAY engine, so a red here localizes to the replay scope and
    # not to the record lane (`create_point` emits a graph event, which is
    # `test_an_armed_guard_does_not_refuse_the_record`'s subject).
    sdk.create_point("fact", "a point the journal replay must reproduce")
    probe = _GuardProbe(armed=True).install(monkeypatch)

    report = sdk._get_proj().rebuild_all(str(tmp_path),
                                        confirm_destructive=True)

    assert probe.refused == [], (
        "the replay engine did not declare itself, so arming the third door "
        "refuses the recovery path: "
        + " | ".join(s[:120] for s in probe.refused))
    assert report, "the replay must report its counts"
    rows = sdk._get_proj().g.query(
        "MATCH (p:Point) RETURN count(p)").result_set
    assert int(rows[0][0]) == 1, (
        "the replay must still reproduce the point from the journal alone")


def test_the_record_projection_runs_inside_the_replay_scope(sdk_with_journal,
                                                            monkeypatch):
    """The record-sourced sibling: wipe, index DDL, `project_records` and the
    declaration are ONE authorized projection of the record (#7971).

    Without the scope, an armed door refuses the wipe — i.e. it refuses
    `rebuild_from_record`, the §2.0 cache-loading path.
    """
    from tortoise import cache_projection as cp

    probe = _GuardProbe(armed=True).install(monkeypatch)
    view = cp.RecordView(
        nodes=(cp.node_from_row(
            "Point", {"id": "p-1", "content": "a projected claim",
                      "point_kind": "claim", "status": "live",
                      "props": {}}),),
        relations=(),
    )

    report = sdk_with_journal._get_proj().rebuild_from_record(
        view, confirm_destructive=True)

    assert probe.refused == [], (
        "the record projection did not declare itself, so arming the third "
        "door refuses the §2.0 cache load: "
        + " | ".join(s[:120] for s in probe.refused))
    assert report["source"] == "record"
    assert report["nodes_written"] == 1


# ── the semantics the declaration claims ──────────────────────────────────


def test_the_counter_is_carried_across_a_rebuild_and_the_rows_are_not_replayed(
        sdk_with_journal):
    """The class is capture-and-restore, NOT a journal projection.

    Stated precisely because the flattering reading is FALSE both ways: the
    `:GraphEventMeta` counter survives the wipe (carried by
    `capture_watermark`/`reestablish_watermark`, which is what makes the lane
    honest), while the `:GraphEvent` ROWS are not replayed (#4664) — so a
    rebuild does not re-derive the record from the journal.
    """
    from tortoise.event_store import capture_watermark, read_after

    sdk = sdk_with_journal
    proj = sdk._get_proj()
    sdk.create_point("fact", "an event whose counter must survive the rebuild")
    assert read_after(proj, 0), "the fixture must have stored an event"

    before = capture_watermark(proj)
    assert before and int(before["last_seq"]) >= 1

    proj.rebuild_all(str(sdk._journal_path_for_test.parent),
                     confirm_destructive=True)

    after = capture_watermark(proj)
    assert after is not None, (
        "the counter was not re-established after the wipe — the declaration's "
        "obligation 2 (capture-and-restore) is now false, so the lane's stated "
        "reason must be rewritten, not this assertion")
    assert int(after["last_seq"]) == int(before["last_seq"]), (
        "the carried counter must be RESTORED, not reset: a reset would re-issue "
        f"seqs already delivered (#4653); before={before}, after={after}")

    assert read_after(proj, 0) == [], (
        "the :GraphEvent ROWS are not replayed today (#4664) — if this ever "
        "returns rows, the class became a real journal projection and the "
        "declaration's stated split should be updated to say so")
