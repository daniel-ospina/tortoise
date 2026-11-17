"""The record's per-graph ordinal ALLOCATOR (#12894, stage C).

THE ONE IDEA. The record allocates the journal position and the journal record
CARRIES it, so `replay()` READS the number instead of deriving it (owner ruling
on `#9190`, 2026-11-15 — comment 6902871297). A counter row is incremented in
the SAME transaction as the row that uses it, so:

* the increment is TRANSACTIONAL — a rollback restores it, no position is
  burned and none is reused-after-being-seen;
* the row lock is held to COMMIT — **allocation order equals commit order**,
  which is the property a bare `nextval()` cannot give (a sequence is not
  transactional and its order is not the commit order);
* `journal_event`'s position insert therefore has no `ON CONFLICT … DO NOTHING`
  to absorb a duplicate: a number is allocated exactly once, so a collision is
  a defect (`23505`) and the transaction must fail rather than drop the payload.

WHAT IS DELIBERATELY *NOT* HERE, because the ruling above does not authorise
it: nothing in this module touches `:GraphEvent.seq` (issued cursors are
`:GraphEvent.seq`), and nothing here bounds `journal_event` (it is the only
carrier of `Object.status`/`Subject.status`, so a retention bound would
resurrect archived entities).

THE ISOLATION CONTRACT. `UPDATE … RETURNING` under the row lock is only
"allocate then commit in that order" at READ COMMITTED. Under REPEATABLE READ or
SERIALIZABLE the blocked UPDATE raises `40001` and there is no retry anywhere on
this path, so a DSN carrying `-c default_transaction_isolation=serializable`
would turn every contended write into an error. The guard reads the
TRANSACTION-SCOPED setting (`current_setting('transaction_isolation')`, which is
the one place it can be read: `default_transaction_isolation` is the session
default and `SET TRANSACTION` only applies to the transaction that issues it),
and REFUSES at arm: `40001` is never legitimately reachable on this path.

NO GLOBAL `statement_timeout`. `57014` is handled as a retryable failure, not
prevented globally: a statement timeout that is short enough to bound an
allocator would also bound legitimate backfill/replay transactions that hold the
same connection for minutes. A caller that wants a bound scopes it locally.

ERROR CLASSES, each observable on :func:`metrics`:

============== ============================================================
`40001`        serialization failure — refused at arm by the isolation guard;
               if one still surfaces it is counted and raised as retryable,
               never retried here (the caller owns the transaction).
`40P01`        deadlock detected — the transaction is aborted by the server, so
               the counter increment rolls back with it; counted, raised
               retryable.
`57014`        statement cancelled (a local timeout, or an operator cancel) —
               as `40P01`: the increment rolls back, counted, raised retryable.
`23505`        duplicate position — the position insert has no DO NOTHING, so
               this is a real defect; counted and raised, NOT absorbed.
============== ============================================================
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from . import record_projection as _rp

LOGGER = logging.getLogger(__name__)

#: The counter table. Named once: the migration, `THE_RECORD_TABLES` and the
#: statements below must not spell it three ways.
RECORD_CURSOR_TABLE = _rp.RECORD_CURSOR_TABLE

#: The allocator's states. `seeded` and `halted` BOTH mean "nobody allocates"
#: and carry OPPOSITE remedies (arm it / investigate it), which is why the mode
#: is a column rather than the presence of a row.
MODE_SEEDED = _rp.MODE_SEEDED
MODE_ACTIVE = _rp.MODE_ACTIVE
MODE_HALTED = _rp.MODE_HALTED
MODES: tuple[str, ...] = (MODE_SEEDED, MODE_ACTIVE, MODE_HALTED)

#: The one transaction-scoped setting that answers "is this safe to allocate
#: on". See the module docstring.
ISOLATION_SQL = "SELECT current_setting('transaction_isolation')"
READ_COMMITTED = "read committed"

SQLSTATE_SERIALIZATION_FAILURE = "40001"
SQLSTATE_DEADLOCK_DETECTED = "40P01"
SQLSTATE_QUERY_CANCELED = "57014"
SQLSTATE_UNIQUE_VIOLATION = "23505"

#: `pgcode` → the metric key that makes the class observable.
RETRYABLE_STATES: dict[str, str] = {
    SQLSTATE_SERIALIZATION_FAILURE: "serialization_failure",
    SQLSTATE_DEADLOCK_DETECTED: "deadlock_detected",
    SQLSTATE_QUERY_CANCELED: "query_cancelled",
}

_COUNTERS: dict[str, int] = {
    "seed_refused": 0,
    "seed_healed_tail": 0,
    "seed_burned": 0,
    "isolation_refused": 0,
    "cursor_not_active": 0,
    "duplicate_position": 0,
    **{key: 0 for key in RETRYABLE_STATES.values()},
}


def metrics() -> dict[str, int]:
    """The allocator's counters. A COPY: a reader must not be able to reset them."""
    return dict(_COUNTERS)


def reset_metrics() -> None:
    """Zero every counter. For tests and for a process that re-arms."""
    for key in _COUNTERS:
        _COUNTERS[key] = 0


class RecordAllocatorError(Exception):
    """Base: the allocation path refused, or failed in a named way."""


class IsolationRefused(RecordAllocatorError):
    """The transaction is not READ COMMITTED; allocation order is not guaranteed."""


class SeedRefused(RecordAllocatorError):
    """The seed wrote no row. :attr:`reason` names which case."""

    #: `divergence` — the record holds positions the journal cannot reproduce
    #: (round-2 B1's refusal); `armed` — the graph is already allocating;
    #: `missing` — no row exists and none could be written (the statement's
    #: guard refused it for a reason the classification could not name).
    def __init__(self, message: str, *, reason: str,
                 row_max: int | None = None,
                 enumerated_floor: int | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.row_max = row_max
        self.enumerated_floor = enumerated_floor


class CursorNotActive(RecordAllocatorError):
    """No cursor row, or its mode is not `active` — nothing is allocated."""


class PositionCollision(RecordAllocatorError):
    """A duplicate position reached the insert. The insert has no DO NOTHING."""


class RetryableAllocationFailure(RecordAllocatorError):
    """`40001` / `40P01` / `57014`. The transaction is aborted by the server and
    the counter increment rolled back WITH it, so the caller may retry the whole
    transaction. This module never retries on its own: it does not own the
    transaction."""

    def __init__(self, message: str, *, sqlstate: str, metric: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.metric = metric


@dataclass(frozen=True)
class SeedResult:
    """What a seed did. `healed_tail` is true when the journal enumerated more
    records than the record held position rows for — the `None`-position tail
    (`#9307`/D-25) that a later replay fills; it is REPORTED, never repaired
    here."""

    org_id: str
    graph_id: str
    seq: int
    mode: str
    row_max: int
    enumerated_floor: int
    #: The journal enumerates MORE records than the record holds rows for
    #: (`floor > row_max`) — a replay fills the tail; benign, and REPORTED.
    healed_tail: bool
    #: The counter is AHEAD of both (`seq > max(row_max, floor)`) — positions were
    #: issued and their rows are gone. NOT the tail; investigate before allocating.
    burned: bool = False


@dataclass(frozen=True)
class Allocation:
    """A contiguous block of positions, `first_seq .. last_seq`, allocated in ONE
    statement. `seqs` is ascending and contiguous (asserted, not assumed)."""

    org_id: str
    graph_id: str
    first_seq: int
    last_seq: int
    seqs: tuple[int, ...]

    @property
    def count(self) -> int:
        return len(self.seqs)


# ── statements ──────────────────────────────────────────────────────────────
#
# ⛔ `%s` ONLY, NEVER `$n`. `record_projection.to_dollar_placeholders` renumbers
# by splitting on `%s`, so a statement that mixes a literal `$1` with `%s` is
# refused by the converter (round-2 finding). The `$n` form exists only at the
# other end of that converter, for PGlite/psql.

#: The seed. THREE properties are carried by the SQL itself, not by prose:
#:
#: 1. `GREATEST(row_max, enumerated_floor)` — never `COALESCE(row_max, 0)` —
#:    because a graph whose journal has N records and ZERO position rows must
#:    seed to N (its next write is N+1). That case is DECIDABLE and is not
#:    deferred: the floor comes from the caller's own replay enumeration.
#: 2. `WHERE observed.row_max <= observed.enumerated_floor` — CASE 2 (the record
#:    holds a position the journal cannot reproduce: a replaced or shortened
#:    journal) is UNREPRESENTABLE: the SELECT yields no row, so no counter row
#:    can be written for it. The Python below only CLASSIFIES that absence.
#: 3. `ON CONFLICT … DO UPDATE … WHERE mode IN ('seeded','halted')` — a seed is
#:    RE-RUNNABLE (the recovery step re-runs it) and MONOTONE (`GREATEST`), and
#:    an `active` graph is refused rather than silently re-based.
#:
#: The row lock: `ON CONFLICT DO UPDATE` is the only statement that can lock an
#: existing counter row here (a fresh INSERT has nothing to lock), and a seed
#: that races a live allocation therefore queues behind it.
SEED_SQL = f"""WITH observed AS (
    SELECT (SELECT COALESCE(MAX(seq), 0) FROM public.journal_event
             WHERE org_id = %s AND graph_id = %s) AS row_max,
           %s::bigint AS enumerated_floor
)
INSERT INTO public.{RECORD_CURSOR_TABLE} AS cursor_row
       (org_id, graph_id, seq, mode)
SELECT %s, %s, GREATEST(observed.row_max, observed.enumerated_floor),
       '{MODE_SEEDED}'
  FROM observed
 WHERE observed.row_max <= observed.enumerated_floor
ON CONFLICT (org_id, graph_id) DO UPDATE
   SET seq = GREATEST(cursor_row.seq, EXCLUDED.seq),
       updated_at = now()
 WHERE cursor_row.mode IN ('{MODE_SEEDED}', '{MODE_HALTED}')
RETURNING seq, mode
"""

READ_SQL = (
    f"SELECT seq, mode FROM public.{RECORD_CURSOR_TABLE} "
    "WHERE org_id = %s AND graph_id = %s"
)

JOURNAL_MAX_SQL = (
    "SELECT COALESCE(MAX(seq), 0) FROM public.journal_event "
    "WHERE org_id = %s AND graph_id = %s"
)

#: How many journal LINES the record holds for one graph — the FLOOR's own
#: quantity, and NOT the same number as :data:`JOURNAL_MAX_SQL`.
#:
#: ⛔ THE TWO ARE DIFFERENT COORDINATES and the distinction is load-bearing for
#: `record_allocator_cli.py sweep` (#13043 review: the sweep reported
#: `floor_is_the_journals_count` while comparing the floor with `MAX(seq)`). A
#: born-uncut graph has to be seeded with the journal's COUNT, because the count
#: is how many positions already exist; `MAX(seq)` is the highest one issued.
#: They agree on a dense axis and diverge the moment a seq is refused or a tail
#: is lost — which is exactly the state the pre-flight exists to report, so
#: answering one of them while naming the other is a false CLEAN.
JOURNAL_COUNT_SQL = (
    "SELECT COUNT(*) FROM public.journal_event "
    "WHERE org_id = %s AND graph_id = %s"
)

#: Graphs this ORG that the RECORD has seen writing, and that have NO counter
#: row — i.e. "born uncut", the state `#13043` review P1-1 named. The creation
#: hook that would prevent it (`provision_graph_record`, the plan's §A7.8 item 8)
#: is not in this tree, so this query is the reconciliation path that makes the
#: state findable instead of merely documented.
#:
#: ⛔ IT READS `journal_event`, NOT `public.graphs`, and the reason is a GRANT,
#: not a preference: `tortoise_record_app` has no privilege on `public.graphs`
#: (the tenancy migration grants the record tables and nothing else), so a sweep
#: over `graphs` would fail `InsufficientPrivilege` on exactly the role the CLI
#: must connect as. The consequence is stated rather than hidden: a graph that
#: has NEVER been written is invisible here — and it has also lost nothing yet,
#: so the first write is the point at which this becomes actionable (and, for a
#: born-uncut graph, the point at which its first line's row is lost).
UNCUT_GRAPHS_SQL = (
    "SELECT DISTINCT j.graph_id FROM public.journal_event j "
    f"LEFT JOIN public.{RECORD_CURSOR_TABLE} c "
    "       ON c.org_id = j.org_id AND c.graph_id = j.graph_id "
    "WHERE j.org_id = %s AND c.graph_id IS NULL ORDER BY j.graph_id"
)

ARM_SQL = (
    f"UPDATE public.{RECORD_CURSOR_TABLE} SET mode = '{MODE_ACTIVE}', "
    "updated_at = now() WHERE org_id = %s AND graph_id = %s "
    f"AND mode = '{MODE_SEEDED}' RETURNING seq"
)

HALT_SQL = (
    f"UPDATE public.{RECORD_CURSOR_TABLE} SET mode = '{MODE_HALTED}', "
    "updated_at = now() WHERE org_id = %s AND graph_id = %s "
    f"AND mode = '{MODE_ACTIVE}' RETURNING seq"
)

REOPEN_SQL = (
    f"UPDATE public.{RECORD_CURSOR_TABLE} SET mode = '{MODE_SEEDED}', "
    "updated_at = now() WHERE org_id = %s AND graph_id = %s "
    f"AND mode = '{MODE_HALTED}' RETURNING seq"
)

#: ⛔ THE ALLOCATION STATEMENT AND ITS MARKER LIVE IN `record_projection`, and
#: this module IMPORTS them. Not a preference: `replay_plan` must be importable by
#: FILE PATH (the PGlite harness loads `record_projection` standalone, so ANY
#: relative import in the code it calls raises `ImportError` — measured, and it
#: broke the schema drill). The plan builder owns every statement in the plan; the
#: allocator owns the operator flows around it. One definition, imported here.
ALLOCATION_SQL = _rp.ALLOCATION_SQL
ALLOCATION_STATEMENT_PREFIX = _rp.ALLOCATION_STATEMENT_PREFIX


# ── the isolation guard ─────────────────────────────────────────────────────

def require_read_committed(conn: Any) -> str:
    """Refuse a transaction whose isolation does not make the lock ordering
    mean what the allocator says it means.

    READ THIS IN THE TRANSACTION, not before it: `current_setting(
    'transaction_isolation')` reports the TRANSACTION's level inside a block and
    the session default outside one. Both are read here, and a caller that
    opens the transaction afterwards is the one shape this cannot see — which is
    why the guard is called at the top of every entry point in this module
    (they all run on the caller's already-open transaction).

    Returns the observed value (normalised: Postgres renders it
    `read committed`). Refusing is the point: at REPEATABLE READ a contended
    UPDATE raises `40001`, and there is no retry on this path.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(ISOLATION_SQL)
        row = cursor.fetchone()
    finally:
        cursor.close()
    observed = str(row[0]) if row else ""
    if observed.strip().lower() != READ_COMMITTED:
        _COUNTERS["isolation_refused"] += 1
        raise IsolationRefused(
            f"the record allocator requires READ COMMITTED (observed "
            f"{observed!r}). Under REPEATABLE READ/SERIALIZABLE the counter's "
            f"blocked UPDATE raises 40001 and nothing on this path retries it, "
            f"so allocation order would stop meaning commit order — refusing "
            f"at arm rather than failing a live write later"
        )
    return observed


def classify(exc: BaseException) -> tuple[str, str] | None:
    """`(sqlstate, metric_key)` for a retryable failure, else `None`.

    Reads the driver's own `pgcode`/`sqlstate` — never message text, which is
    localised and version-dependent."""
    sqlstate = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
    if sqlstate and sqlstate in RETRYABLE_STATES:
        return sqlstate, RETRYABLE_STATES[sqlstate]
    return None


def raise_named(exc: BaseException, *, where: str) -> None:
    """Translate a driver failure into this module's vocabulary, counting it.

    `23505` is a COLLISION (the position insert has no DO NOTHING); the three
    retryable states are the caller's to retry. Any OTHER failure returns
    `None`, so the caller keeps its own attribution (statement, table, columns)
    instead of receiving a guess from here."""
    named = classify(exc)
    if named is not None:
        sqlstate, metric = named
        _COUNTERS[metric] += 1
        raise RetryableAllocationFailure(
            f"{where} failed with {sqlstate} — the transaction (and the counter "
            f"increment with it) is aborted; retry the whole transaction",
            sqlstate=sqlstate,
            metric=metric,
        ) from exc
    sqlstate = getattr(exc, "pgcode", None) or getattr(exc, "sqlstate", None)
    if sqlstate == SQLSTATE_UNIQUE_VIOLATION:
        _COUNTERS["duplicate_position"] += 1
        raise PositionCollision(
            f"{where} hit a duplicate journal position (23505). The position "
            f"insert has no ON CONFLICT DO NOTHING on this path on purpose: an "
            f"allocated number is unique, so a duplicate means the counter is "
            f"behind the table. `seed` and `reopen` both REFUSE from "
            f"'{MODE_ACTIVE}' (`SEED_SQL`'s conflict path is scoped to "
            f"'{MODE_SEEDED}'/'{MODE_HALTED}'; `REOPEN_SQL` to '{MODE_HALTED}'), "
            f"so `halt` it FIRST (`{MODE_ACTIVE} → {MODE_HALTED}`), then re-seed "
            f"(monotone `GREATEST`), `reopen` (`{MODE_HALTED} → {MODE_SEEDED}`) "
            f"and `arm` (`{MODE_SEEDED} → {MODE_ACTIVE}`) before allocating again "
            f"— `arm` is the last step because `ALLOCATION_SQL` is scoped to "
            f"'{MODE_ACTIVE}', so reopening alone leaves the counter unallocatable"
        ) from exc


# ── the seed ────────────────────────────────────────────────────────────────

def seed(conn: Any, *, org_id: str, graph_id: str,
         enumerated_floor: int) -> SeedResult:
    """Seed the counter for one graph from the durable facts.

    `enumerated_floor` is the number of records a replay of THIS graph's journal
    enumerates (the caller's fact, from the journal's own reader — so it is
    segment-aware, not one file's index).

    Cases, all decided by SQL (see :data:`SEED_SQL`):

    * empty graph — `row_max = 0`, `floor = 0` ⇒ `seq = 0`, mode `seeded`;
    * journal with records and NO position rows — `seq = floor`, so the next
      write takes `floor + 1` (the `None`-position tail is REPORTED as
      `healed_tail`, not repaired here);
    * `row_max > floor` (round-2 case 2: the record holds a position the journal
      cannot reproduce) — the statement CANNOT write a row; this raises
      :class:`SeedRefused` with `reason="divergence"` and both numbers, and the
      graph stays on whatever allocator it had (absence of a counter row IS
      "not cut over");
    * already `active` — refused (never silently re-based);
    * re-seed of a `seeded`/`halted` row — allowed and MONOTONE.

    The seed SETS AND VERIFIES the tenant GUC ITSELF (:func:`_scope`, the same
    contract :func:`record_projection.scope_connection` implements). ⛔ BE PRECISE
    ABOUT WHAT THAT PREVENTS: an unset (or wrong) `app.current_org_id` does NOT
    produce a divergence — `SEED_SQL` is refused by the policy with
    `42501 InsufficientPrivilege`, so `written is None` is never reached and
    `reason="divergence"` stays reachable ONLY through `row_max > floor` (a
    replaced or shortened journal). Measured, and asserted by
    `test_a_seed_without_the_guc_is_a_refusal_not_a_divergence`. What `_scope`
    buys is that the gap is a REFUSAL AT THE TOP rather than a raw driver error
    from the middle of the statement. It is safe to do here and not in the
    generic plan: the seed runs in the caller's transaction, so the scope it
    sets and the scope the plan then uses cannot disagree.
    """
    require_read_committed(conn)
    _scope(conn, org_id)
    floor = int(enumerated_floor)
    if floor < 0:
        raise RecordAllocatorError(
            f"enumerated_floor must be >= 0 (got {enumerated_floor!r}); it is "
            f"the number of records a replay of this journal enumerates")

    cursor = conn.cursor()
    try:
        cursor.execute(SEED_SQL, (org_id, graph_id, floor, org_id, graph_id))
        written = cursor.fetchone()
        row_max = _journal_max(cursor, org_id, graph_id)
        if written is None:
            existing = _read_cursor(cursor, org_id, graph_id)
            _COUNTERS["seed_refused"] += 1
            # ⛔ THE ABSENCE HAS TWO CAUSES AND THEY HAVE OPPOSITE REMEDIES, so
            # the classification is by the GUARD's own condition, not by "a row
            # already exists". `row_max > floor` is case 2 (the SQL's guard is
            # what refused it, on a graph with or without a counter row);
            # otherwise the counter exists in a mode the ON CONFLICT UPDATE
            # would not touch (`active`).
            if row_max > floor:
                raise SeedRefused(
                    f"the seed wrote no counter row for {org_id}/{graph_id}: "
                    f"the record holds position {row_max} but a replay of the "
                    f"journal enumerates only {floor}. The record claims "
                    f"positions this journal cannot reproduce (a replaced or "
                    f"shortened journal), so the cutover is REFUSED — capture "
                    f"both numbers and do nothing else: no counter row means "
                    f"the graph is not cut over and keeps working",
                    reason="divergence", row_max=row_max,
                    enumerated_floor=floor,
                )
            if existing is None:
                raise SeedRefused(
                    f"the seed wrote no counter row for {org_id}/{graph_id} "
                    f"and the graph has none: the statement's guard could not "
                    f"be classified (row_max={row_max}, "
                    f"enumerated_floor={floor})",
                    reason="missing", row_max=row_max, enumerated_floor=floor,
                )
            raise SeedRefused(
                f"the seed did not write {org_id}/{graph_id} because its "
                f"counter is already {existing[1]!r} at seq {existing[0]} — "
                f"re-seeding an allocating graph would re-base a live "
                f"allocator; halt it first if that is what you mean",
                reason="armed", row_max=row_max, enumerated_floor=floor,
            )
        seq, mode = int(written[0]), str(written[1])
    finally:
        cursor.close()

    # ⛔ TWO FACTS, TWO NAMES. A TAIL is the journal enumerating MORE records than
    # the record holds position rows for (`floor > row_max`) — a replay fills it,
    # benign. A BURN is the counter being AHEAD of everything the record and the
    # journal can account for (`seq > max(row_max, floor)`): positions were issued
    # and their rows are gone. Reporting the second as the first sent an operator
    # to the wrong remedy, which is the misreading this module refuses elsewhere.
    healed_tail = floor > row_max
    burned = seq > max(row_max, floor)
    if healed_tail:
        _COUNTERS["seed_healed_tail"] += 1
    if burned:
        _COUNTERS["seed_burned"] += 1
    return SeedResult(org_id=org_id, graph_id=graph_id, seq=seq, mode=mode,
                      row_max=row_max, enumerated_floor=floor,
                      healed_tail=healed_tail, burned=burned)


def arm(conn: Any, *, org_id: str, graph_id: str) -> int:
    """`seeded → active`. One writer per transition: every other starting mode
    is refused rather than silently moved."""
    return _transition(conn, ARM_SQL, org_id=org_id, graph_id=graph_id,
                       wanted=MODE_SEEDED, target=MODE_ACTIVE)


def halt(conn: Any, *, org_id: str, graph_id: str) -> int:
    """`active → halted`. The counter is KEPT: the carried numbers are the only
    record of what was issued."""
    return _transition(conn, HALT_SQL, org_id=org_id, graph_id=graph_id,
                       wanted=MODE_ACTIVE, target=MODE_HALTED)


def reopen(conn: Any, *, org_id: str, graph_id: str) -> int:
    """`halted → seeded`, so a halted graph can be re-seeded then re-armed."""
    return _transition(conn, REOPEN_SQL, org_id=org_id, graph_id=graph_id,
                       wanted=MODE_HALTED, target=MODE_SEEDED)


def _transition(conn: Any, sql: str, *, org_id: str, graph_id: str,
                wanted: str, target: str) -> int:
    require_read_committed(conn)
    _scope(conn, org_id)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (org_id, graph_id))
        row = cursor.fetchone()
        if row is None:
            existing = _read_cursor(cursor, org_id, graph_id)
            observed = "absent" if existing is None else existing[1]
            raise RecordAllocatorError(
                f"{org_id}/{graph_id} cannot move to {target!r}: its mode is "
                f"{observed!r}, not {wanted!r}. A transition has exactly one "
                f"writer — choosing the wrong mode silently moves a graph "
                f"between `seeded` (not cut over) and `halted` (stopped after "
                f"a defect), which have opposite remedies"
            )
        return int(row[0])
    finally:
        cursor.close()


# ── allocation ──────────────────────────────────────────────────────────────

#: ⛔ NO SUPPRESSION COUNTER HERE. An earlier cut of this change counted positions
#: the replay path's `DO NOTHING` absorbed, because that absorption was silent.
#: `#9318` (PR #12904, merged on `main` while this lane was open) made it a TYPED
#: refusal instead — `record_projection._refuse_a_journal_collision` asks the
#: database whether the surviving row's content differs and raises
#: `JournalPositionCollision`, which the live door reports as the `journal_collision`
#: refusal token. A second counter here would be a second opinion about a verdict
#: the record already owns, and the benign half (a genuine re-replay) is a
#: documented no-op that must stay quiet. The ALLOCATED path needs neither: its
#: insert has no `DO NOTHING`, so a duplicate is a `23505` that rolls the
#: increment back.


def read_cursor(conn: Any, *, org_id: str,
                graph_id: str) -> tuple[int, str] | None:
    """`(seq, mode)` for one graph, or `None` when it is not cut over.

    ⛔ IT SCOPES THE CONNECTION. `record_cursor`'s policy filters by
    `app.current_org_id`, so an unscoped read returns ZERO ROWS and this function
    would report "not cut over" for a graph that is allocating — the silent
    zero-row shape `#9600` cycle 1 closed for UPDATE-only plans, reintroduced in
    this module's own reader.
    """
    _scope(conn, org_id)
    cursor = conn.cursor()
    try:
        return _read_cursor(cursor, org_id, graph_id)
    finally:
        cursor.close()


def allocation_statement(*, org_id: str, graph_id: str,
                         records: Sequence[Mapping[str, Any]]) -> Any:
    """The ONE statement that allocates `len(records)` positions and inserts the
    rows carrying them, in plan order.

    Returns a `record_projection.Statement`. ⛔ The import is NOT lazy in THIS
    module: `record_allocator` imports `record_projection` at module level
    (`_rp`, and the constants re-exported below come from it). The lazy arm is
    on the OTHER side and it is load-bearing for a SPECIFIC reason: the PGlite
    schema drill loads `record_projection.py` BY PATH (no package) and calls
    `replay_plan`, so a relative import in a function THAT PATH REACHES raises
    `ImportError` and the drill dies (measured on the first version of #12894).
    `record_projection.replay`'s own in-function import is safe precisely because
    the drill never calls `replay` — do not "simplify" either side.

    ⚠️ THE PAYLOADS COME FROM `record_projection.journal_payload`, the SAME
    function `_journal_row` uses, so the allocated path and the replay path
    cannot disagree about what a journal row contains.
    """
    return _rp.allocation_statement(
        org_id=org_id, graph_id=graph_id, records=records)


def allocation_from_seqs(seqs: Iterable[int], *, org_id: str,
                         graph_id: str) -> Allocation:
    """Build (and VALIDATE) an :class:`Allocation` from returned positions.

    Empty means nothing was allocated — no counter row, or its mode is not
    `active` — which is a refusal, never a number to invent. Contiguity is
    ASSERTED: a caller that assumed it would key rows the journal never carried.
    """
    positions = tuple(int(seq) for seq in seqs)
    if not positions:
        _COUNTERS["cursor_not_active"] += 1
        raise CursorNotActive(
            f"no position was allocated for {org_id}/{graph_id}: there is no "
            f"counter row, or its mode is not '{MODE_ACTIVE}'. An allocation "
            f"under `seeded` means the cutover was never armed; under `halted` "
            f"it means a defect stopped it. Neither is a number to invent"
        )
    first, last = min(positions), max(positions)
    if len(set(positions)) != len(positions) or last - first + 1 != len(positions):
        raise RecordAllocatorError(
            f"the allocation for {org_id}/{graph_id} returned {positions!r} — not "
            f"a contiguous block, so at least one row carries a position the "
            f"counter never issued"
        )
    return Allocation(org_id=org_id, graph_id=graph_id, first_seq=first,
                      last_seq=last, seqs=positions)


def execute_allocation(conn: Any, statement: Any, *, org_id: str,
                       graph_id: str) -> Allocation:
    """Execute the allocation statement and return the block it produced.

    ⛔ THE ISOLATION GUARD RUNS HERE TOO. `replay(allocate=True)` BUILDS the plan
    first (`replay_plan` only emits statements, it executes none) and then guards
    — so the refusal still lands before the first statement runs, which is the
    property that matters. But this is a PUBLIC entry point (the module's own tests call
    it directly) and a caller that skips the guard on a SERIALIZABLE DSN would get
    `40001` per contended write instead of one arm-time refusal — with nothing on
    the path retrying it.
    """
    # ⛔ IT SCOPES THE CONNECTION FIRST, for the same reason `seed()` does. The
    # counter's policy filters on `app.current_org_id`, so an unscoped UPDATE
    # matches ZERO rows and this function raises `CursorNotActive` — a refusal
    # that says "the cutover was never armed" about a graph that IS armed. A
    # mis-scope must not be presented as a state (#12894 review P2, the same trap
    # the seed was fixed for).
    _scope(conn, org_id)
    require_read_committed(conn)
    cursor = conn.cursor()
    try:
        try:
            cursor.execute(statement.sql, statement.params)
            rows = cursor.fetchall()
        except Exception as exc:  # classified, then re-raised as this module's
            raise_named(exc, where="the position allocation")
            raise
    finally:
        cursor.close()
    return allocation_from_seqs((row[0] for row in rows), org_id=org_id,
                                graph_id=graph_id)


def execute_allocations(conn: Any, statements: Iterable[Any], *, org_id: str,
                        graph_id: str) -> list[Allocation]:
    """Convenience for a plan with at most one allocation statement."""
    out = [execute_allocation(conn, statement, org_id=org_id, graph_id=graph_id)
           for statement in statements]
    return out


# ── helpers ─────────────────────────────────────────────────────────────────

def _scope(conn: Any, org_id: str) -> None:
    """Set (and VERIFY) the tenant GUC on the caller's transaction.

    `record_projection` owns the scope prologue and the role gate, and this
    module ALREADY imports it at module level (`_rp`, whose constants are
    re-exported below) — so there is nothing to defer here, and the import that
    actually has to be lazy is on the OTHER side: `record_projection.replay`
    imports THIS module inside the function, because
    `supabase/tests/pglite/validate.mjs` loads `record_projection.py` BY PATH and
    calls `replay_plan` — the first version of #12894 imported `record_allocator`
    inside `replay_plan` and the drill died at `#7969 record projection`
    (measured). State the direction, so a lane does not "simplify" the wrong one.
    """
    _rp.scope_connection(conn, org_id)


def scope_tenant(conn: Any, org_id: str) -> None:
    """Set the tenant GUC for a READER that is not seeding or allocating.

    ⛔ A SWEEP WITHOUT THIS REPORTS A FALSE CLEAN (#13043 P1-1, measured):
    `record_cursor` and `journal_event` carry policies that filter on
    `app.current_org_id`, so an unscoped connection returns ZERO ROWS — and a
    "which graphs are born uncut" query that returns zero rows reads as "none
    are", which is the most dangerous possible answer for a pre-flight. The
    tool's own reconciliation verb therefore scopes with the SAME prologue the
    writer uses (public because it is a legitimate reader's need, not a test
    hook).
    """
    _scope(conn, org_id)


def _read_cursor(cursor: Any, org_id: str,
                 graph_id: str) -> tuple[int, str] | None:
    cursor.execute(READ_SQL, (org_id, graph_id))
    row = cursor.fetchone()
    return (int(row[0]), str(row[1])) if row else None


def _journal_max(cursor: Any, org_id: str, graph_id: str) -> int:
    cursor.execute(JOURNAL_MAX_SQL, (org_id, graph_id))
    row = cursor.fetchone()
    return int(row[0]) if row else 0
