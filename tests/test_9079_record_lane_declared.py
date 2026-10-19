"""#9079 §17.1 — the writers that reach `proj.g` declare themselves: the EVENT
STORE and the two REPLAY engines.

`docs/architecture/STORAGE-ARCHITECTURE.md` §17.1 (the §2.0 owner ruling) splits
the store: the journal is the system of record, the graph is a derived cache, so
"every write goes to the truth store first, and the cache is projected from it".
`#7951` enforced that at the fold and at the public raw-query lane; `#9079`
declares the writers that must not be refused when the third door — the
projection's own write handle, `proj.g` (`_GuardedGraph`) — is CLOSED. That
door is still open (`#9124`, not on `main`), which is why the declarations land
first.

Two writer classes reached that handle with NO declaration, and this suite pins
both. Neither is a violation of §17.1; both are writers the door will refuse,
and refusing them is a REGRESSION, not enforcement:

1. **The event store** (`tortoise/event_store.py`) is a second DURABLE surface
   living in the cache graph — the change-feed log (`:GraphEvent`) and its seq
   allocator (`:GraphEventMeta`) — which no journal record describes, so the
   fold's gate cannot even recognise it. Refusing its append does not protect
   the record; it DELETES the change feed, and it does so SILENTLY:
   `tortoise/sdk.py::_emit_event` calls this store inside
   `except Exception: _logger.warning("event emission failed … continuing")`, so
   a refusal here stops every graph event with only a warning line. ⚠️ The lane
   is a declared CARVEOUT, not a claim of rebuild durability: a rebuild wipes
   these ROWS and does not replay them — the behaviour `#4664` names, CLOSED on
   2026-09-25 as consolidated into the also-closed `#5048` and STILL observable
   here (the last test measures it) — with only the allocator carried forward.
2. **The replay engines** (`FalkorProjection.rebuild_all`,
   `.rebuild_from_record`). Their sibling `rebuild` has opened
   `_replay_write_scope()` since `#7951` (`with _replay_write_scope():
   self._replay_log(…)`); these two issued the wipe, the folds, the index DDL
   and the record projection straight at the handle with no scope at all — so
   closing the door would refuse every journal and record rebuild.

What this suite pins, and why each assertion is not prose:

* every MUTATING statement the event store issues observes a named lane AT THE
  HANDLE — recorded from inside the handle, so a caller that merely succeeds
  today cannot pass;
* an ARMED third door (the guard's own predicate, reproduced verbatim) does not
  refuse the event store and does not refuse either replay engine — while it
  still refuses an undeclared `:Point` write through the same handle, so a lane
  cannot be cited as a reason to widen the guard (the `#9068` shape);
* the lane claims NO rebuild durability, and the suite says which half survives:
  a rebuild carries the `:GraphEventMeta` counter forward (so `next_seq` cannot
  re-issue a delivered seq) and DESTROYS the `:GraphEvent` rows without
  replaying them — so the claim is "the allocator is carried, the log is not",
  never "the record survives a rebuild".

It does not arm `#9124`'s guard (that child is not on `main`). `_GuardProbe`
reproduces the guard's PREDICATE — journal-backed + no declaration in flight + a
mutating statement ⇒ refused — the same way
`tests/test_9079_event_writers_journal.py` does, so "these writers will not be
refused" is executable now instead of blocked on a sibling, and the two cannot
disagree about what a declaration is.

MUTATION for the load-bearing tests:

* drop `_record_write` in `tortoise/event_store.py` (call `g.query` directly
  again) → TWO tests go red. `test_every_event_store_write_declares_the_record_lane`
  fails on observed `None` lanes; `test_an_armed_guard_does_not_refuse_the_event_store`
  fails on FOUR refusals whose first is
  `CREATE INDEX FOR (n:GraphEvent) ON (n.event_id)` and whose fourth is
  `next_seq`'s MERGE — the `append_event` CREATE is never reached, because
  `_emit_event` wraps the schema DDL, the allocator and the append in ONE `try`
  and `ensure_event_schema` swallows each DDL refusal individually, so it is the
  ALLOCATOR that aborts the block. The width test deliberately stays GREEN
  under this mutation: its assertion is membership-based, so an event-store
  defect cannot reach it (that is what makes it about lane width);
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
#: silently is a lane the next reader cannot find — the name is what THIS SUITE
#: reads back at the write handle and what a source grep turns up. (A
#: `DirectCacheWriteRefused` deliberately does not render it: a refusal fires
#: only when NO scope is in flight, so there is no reason to print.)
RECORD_LANE = "event-store.record"

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
    reaches BOTH deletes and `_refresh_first_seq`; the watermark path reaches
    the monotone MERGE.

    ⚠️ Order matters, and it is why the log is re-seeded between the two
    purges: `purge_expired(retention_days=0)` deletes EVERY event, so a
    `purge_overflow` immediately after finds `total == 0` and returns BEFORE
    issuing its own delete — leaving that statement unpinned while the suite
    still looked green.
    """
    from tortoise.event_store import (
        capture_watermark,
        purge_expired,
        purge_overflow,
        reestablish_watermark,
    )

    proj = sdk._get_proj()
    sdk.create_point("fact", "an event the event-store lane must be able to store")
    sdk.create_point("fact", "a second event, so the seq allocator bumps twice")
    purge_expired(proj, retention_days=0)
    # Re-seed so `purge_overflow` actually computes an overflow and issues its
    # DELETE — otherwise that statement is never exercised (see the note above).
    sdk.create_point("fact", "an event for the size-cap purge")
    purged_by_cap = purge_overflow(proj, max_events=0)
    assert purged_by_cap == 1, (
        "the size-cap purge must actually run — with an empty log it returns "
        f"early and its DELETE is never exercised; observed={purged_by_cap}")
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

    # NON-VACUOUS, at STATEMENT granularity rather than per family: every one of
    # the store's mutating statements carries its own distinguishing fragment, so
    # a statement that stops being exercised (or stops being declared) cannot
    # hide behind a sibling that shares its family. Two statements were genuinely
    # unpinned under the family-keyed census this replaces: `_refresh_first_seq`'s
    # SET matched NO family at all, and `reestablish_watermark`'s MERGE was
    # indistinguishable from `next_seq`'s (one predicate satisfied both). (The
    # purges were already pinned by their own fragments.)
    statements = {
        "schema:event_id index": "ON (n.event_id)",
        "schema:unique constraint": "GRAPH.CONSTRAINT CREATE",
        "schema:seq index": "ON (n.seq)",
        "allocator (next_seq)": "m.last_seq AS prev",
        "append (GraphEvent)": "CREATE (e:GraphEvent",
        "purge_expired": "n.ts < $cutoff",
        "purge_overflow": "ORDER BY n.seq ASC",
        "refresh_first_seq": "SET m.first_seq = CASE",
        "reestablish_watermark": "ON CREATE SET m.last_seq = $last",
    }
    # Each fragment must identify exactly ONE statement: `any()`/membership alone
    # would let a fragment that also matches a sibling's statement keep the
    # census green while the REAL statement stopped being exercised — the
    # family-keyed defect this per-statement split replaced.
    hits = {name: {stmt for stmt, _lane in observed if fragment in stmt}
            for name, fragment in statements.items()}
    ambiguous = [name for name, hit in hits.items() if len(hit) != 1]
    assert not ambiguous, (
        "each fragment must identify exactly ONE statement; ambiguous: "
        + " | ".join(f"{n} -> {len(hits[n])}" for n in ambiguous))
    pinned = {next(iter(hit)) for hit in hits.values()}
    assert len(pinned) == len(statements), (
        f"the census must pin {len(statements)} DISTINCT statements; it pinned "
        f"{len(pinned)} — a fragment is matching a sibling's statement")

    missing = [name for name, fragment in statements.items()
               if not any(fragment in s for s, _lane in observed)]
    assert not missing, (
        f"the exercise did not reach {missing} — the coverage claim in this "
        "test would be vacuous. Statements observed: "
        + " | ".join(s[:80] for s, _lane in observed))


def test_an_armed_guard_does_not_refuse_the_event_store(sdk_with_journal,
                                                       monkeypatch):
    """The indicator that matters for #9079: the SDK's writes still succeed.

    The guard's predicate is armed verbatim over the handle. The declaration is
    what keeps a create from dying on the event store's own append — and a
    create that died here would be invisible, because `_emit_event` swallows
    the exception.
    """
    from tortoise.event_store import read_after

    probe = _GuardProbe(armed=True).install(monkeypatch)
    point = sdk_with_journal.create_point(
        "fact", "a create with the third door armed")

    assert point["id"]
    assert probe.refused == [], (
        "the event store did not declare itself, so arming the third door "
        "refuses its own append: "
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
    """A declared lane is a lane, not an open handle: after a DECLARED write
    the authorization is closed again, and an undeclared writer is refused by
    the very same armed guard.

    Without this, `event-store.record` could be cited as a reason to widen the
    guard ("some writers do not journal, so do not close the door"), which is
    the licence `#9068` records as the failure shape to avoid. The declared
    write runs FIRST on purpose: it is what makes this a test about the lane's
    WIDTH — a scope that leaked (stayed set past its `with`) would authorize
    the following `:Point` write and this fails — rather than a test about the
    probe's own refusal.
    """
    probe = _GuardProbe(armed=True).install(monkeypatch)
    sdk = sdk_with_journal
    proj = sdk._get_proj()

    # A real DECLARED write through the same armed handle: it must land, and
    # the lane must close when its `with` exits.
    point = sdk.create_point("fact", "a declared write, then a probe of the width")
    assert point["id"]
    assert _CACHE_WRITE_AUTHORIZED.get() is None, (
        "the lane leaked: after the declared write the authorization is still "
        f"{_CACHE_WRITE_AUTHORIZED.get()!r}, so any later write would be "
        "authorized — a sticky scope is the global-bypass failure shape")

    with pytest.raises(DirectCacheWriteRefused):
        proj.g.query("CREATE (n:Point {id:'undeclared-demo'})")

    assert any("undeclared-demo" in s for s in probe.refused), (
        "the armed probe did not refuse the undeclared `:Point` write; "
        "refused: " + " | ".join(s[:120] for s in probe.refused))

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
    # `test_an_armed_guard_does_not_refuse_the_event_store`'s subject).
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
    """The ALLOCATOR is carried; the ROWS are destroyed and not replayed.

    Stated precisely because the flattering reading is false: this lane does
    NOT make the store rebuild-durable. Only `:GraphEventMeta.last_seq` is
    capture-and-restored (`capture_watermark` before the wipe,
    `reestablish_watermark` after) so `next_seq` cannot re-issue a delivered
    seq (#4653); the `:GraphEvent` rows are wiped and are NOT replayed (#4664).
    If the rows ever come back from a rebuild this assertion flips, and the
    lane's stated scope should then be updated to say so.
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
