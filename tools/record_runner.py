"""Run the record fold — Interface A with an operator in front of it (`#7879`).

WHY THIS EXISTS
===============
`tortoise/record_projection.py` is complete, total and idempotent, and until this
file **nothing ran it**. Measured 2026-11-17 on `main` `8762578f8c` (the hole is
recorded at `#7879` comment 2026-11-17): the module had **no ``__main__``, no
argparse and no ``main()``**, no tool, workflow or cron invoked it, and its
``replay()`` was reachable only from tests. The consequences were measured, not
theorised: every dense read fell through to the graph, where ``:Object`` /
``:Subject`` / ``:Event`` / ``:Source`` carry no vector index — ``scan_fallback``
on 100 % of samples, p50 34 ms at rest, p99 634 ms under load — so the HNSW
index we paid to build answered nothing and the machine that answered was the
one the epic exists to move away from.

This is hole **1** of three. Hole 2 (an ``apply`` for the graph's historic
payload) is served by this file's ``apply``: the journal is the record's own
source (``derived tables = replay(the journal)``, §3), it is append-only, and
every SDK write appends to it — so replaying it into Postgres *is* moving the
historic payload. Hole 3 (wiring live writes) is deliberately NOT here: it is
the higher-risk change and it lands on its own evidence (see the PR body).

WHAT IT IS, AND WHAT IT IS NOT
==============================
* **It is a driver, not a second fold.** Every statement comes from
  :func:`tortoise.record_projection.replay`; this file chooses *which records*
  and *which transaction*, and it never builds a mutation. A second fold would
  be a second answer to "what does this event mean in Postgres".
* **It never commits the projection ALONE.** §3's invariant spans the journal
  append and this projection, so the batch is the transaction: one batch, one
  commit, and a failure rolls the whole batch back. That is also what makes a
  batch the resumption unit.
* **It is not the write chokepoint** (§2.3 / Interface C, `#7951`). It does not
  route live writes and it does not append the journal; it folds a journal that
  already exists.
* **It does not delete, prune or "clean" the graph.** `#10175` forbids it and
  `#5333` governs customer memories. This tool opens no graph connection at all.

THE CHECKPOINT IS DERIVED, NOT STORED
=====================================
A resumable backfill needs a cursor, and the obvious implementation — a
``record_runner_checkpoint`` table updated beside each batch — is the one that
can be wrong: two writes in one transaction can still be separated by a crash in
the *reader's* understanding, and a cursor that disagrees with the effects is a
cursor that skips or repeats silently.

So this tool keeps **no cursor of its own**. The record already writes one:
``public.journal_event`` is keyed ``(org_id, graph_id, seq)``, it is
append-only (a trigger enforces it), and ``replay()`` writes the position row in
the SAME batch transaction as that record's layer mutations. Position present
therefore *means* the record consumed it, and the checkpoint is a **read** of the
record:

    next_seq = the first missing position in 1..max(seq), else max(seq) + 1

Four properties follow, and none of them needs a second table:

1. **It cannot drift.** There is no second value to keep in step.
2. **It advances only after the effects are durable**, because it *is* the
   effects — the research's "commit the checkpoint atomically with the batch" is
   satisfied structurally rather than procedurally.
3. **A crash repeats at most one batch**, and repeating it is safe because
   ``replay()`` is idempotent.
4. **A re-run is a no-op that SAYS SO**: a fully-consumed journal consumes 0
   events and executes 0 statements, and the report states both counts. It does
   not "replay and rely on ``ON CONFLICT``" — it declines to replay at all, and
   the idempotency of the fold is the backstop, not the mechanism.

A **gap** (a missing position below the high-water mark) is reported, never
papered over: it means the record is short of positions it should hold, and the
run resumes AT the gap rather than after the highest one.

THE TWO GATES BEFORE A WRITE
============================
1. **The plan must exist and have been read.** ``apply`` refuses without
   ``--acknowledge-plan N``, where ``N`` is the number of events the plan
   reported. This is ``CURATION-11469.md``'s harness guarantee — *report before
   drop* — made mechanical: you cannot acknowledge a number you have not seen,
   and a journal that grew since the plan produced a different number, which is a
   refusal naming both. ``--limit`` gives a bounded, stable run when the journal
   is moving.
2. **``plan`` opens no database at all** unless asked for the checkpoint. The
   "``--dry-run`` writes zero rows" property is therefore structural: the code
   path that writes is not reachable from it.

Every batch is planned (``replay_plan``, pure) and its summary printed *before*
the batch's statements execute, so within a run the plan also precedes the write.

EXIT CODES — `record_allocator_cli`'s contract, so a chain reads one way
=======================================================================
* ``0``  the run did what it said: a plan produced, or a batch set applied and
  committed (including "nothing to consume").
* ``1``  REFUSED — nothing was written, or the write could not be PROVEN: no
  record armed, a journal that does not exist, a record that holds positions
  this journal does not have, an unacknowledged/stale plan, a cut-over graph
  refusing an enumerated replay, a batch that raised (rolled back), or a commit
  that raised (no batch is claimed).
* ``2``  USAGE — argparse rejected the invocation, or a bound it could not
  honour (``--limit``/``--batch-size`` below 1) refused BEFORE anything was
  read. These are facts about the INVOCATION, not about the record, so they must
  not wear the record's verdict — a chain that reads ``1`` halts a healthy
  backfill over a typo in its own flags.

``--json`` prints the same report as JSON on stdout and nothing else.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below. A module-level
# 3.11+-only import raises ImportError before a later guard could run, so the
# guard is the first statement after the docstring — which is what
# `tests/test_entry_point_python_guard.py` asserts over every `tools/**/*.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    # #4053: an old interpreter is an ENVIRONMENT failure, not this tool's own
    # verdict. Exit 1 is REFUSED, which a consumer reads as "the record must not
    # be applied" — a claim about the record, when the truth is about the box.
    # `main()` never RETURNS 2 — `_UsageParser.error` and
    # `_refuse_impossible_bounds` raise it, while `main()` turns a `RunnerError`
    # into 1. So 2 is the one code in this tool's set that cannot wear a verdict
    # about the record. The constants are defined below the imports, hence the
    # literal here.
    print(
        f"record_runner: requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/record_runner.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)  # EXIT: USAGE — not 1 (REFUSED, the tool's own verdict)

import argparse
import contextlib
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2

#: Records per transaction. A batch is the resumption unit, so this bounds how
#: much work a crash can repeat and how long a row-lock can be held — not a
#: quality knob. 200 keeps a commit well inside the default statement timeout on
#: the measured journal while making a replayed batch cheap.
DEFAULT_BATCH_SIZE = 200

#: The batch transaction's own bound. Larger than the serve bound (a served read
#: must answer a request; a backfill must finish a batch), and still bounded so a
#: wedged database cannot hold a lane forever. Transaction-local, like every
#: other bound this subsystem sets.
DEFAULT_STATEMENT_TIMEOUT_MS = 300_000


class RunnerError(Exception):
    """A refusal. Raised where a run must stop WITHOUT writing, or stop a batch."""


# ── the journal ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Journal:
    """What the journal reader returned, and what it had to say about damage.

    ``segmented`` is reported because it changes what "the whole journal" means:
    a single file is one segment, a segmented layout is every present one, and a
    reader that silently counted only the active file would fold a fraction of
    the record. ``corrupt``/``torn`` are the reader's own counters, surfaced so a
    run over damaged input is never mistaken for a complete one.
    """

    path: Path
    records: list[dict] = field(default_factory=list)
    segmented: bool = False
    salvage: bool = False
    #: ``None`` = NOT REPORTED (a segmented layout: its reader does not return
    #: the counts), which must never be rendered as ``0``.
    corrupt: int | None = 0
    torn: int | None = 0

    def __len__(self) -> int:
        return len(self.records)


def read_journal(path: str | Path, *, salvage: bool = False) -> Journal:
    """The journal, read by the JOURNAL'S OWN reader — never a second one.

    A second reader is a second answer to "how long is the journal", and the
    number it produces becomes a ``seq`` that live writes then key on (the reason
    ``record_allocator_cli._journal_floor`` refuses a single-file count of a
    segmented layout). So this function dispatches on the LAYOUT and calls the
    reader that owns it:

    * a segmented directory (a manifest, or any sealed sibling) →
      :func:`tortoise.journal_segments.read_all_segments`, which verifies the
      sequence before reading and returns every present segment in replay order;
    * anything else → :class:`tortoise.log.EventLog`'s ``read_all``.

    ``salvage`` is **off by default** and that is the fail-closed direction: the
    strict read raises on a malformed MID-FILE line, where a salvage read skips
    it. A fold that skipped a line would produce a record missing a mutation and
    would report success, so salvaging is an explicit, recorded choice and the
    report says when it was taken.
    """
    from tortoise import journal_segments
    from tortoise.log import EventLog

    given = Path(path).expanduser()
    # ⛔ THE EXISTENCE TEST COMES FIRST (review P2). It used to run AFTER
    # `graph_dir.iterdir()`, so a mistyped DIRECTORY escaped as the driver's own
    # `FileNotFoundError: /typo/dir` instead of this branch's crafted refusal —
    # and the test only ever used a missing file inside an EXISTING parent, so it
    # passed. The message below is the whole reason the branch exists.
    if not given.exists():
        raise RunnerError(
            f"journal {given} does not exist — a graph with no journal has "
            f"nothing to fold, and a mistyped path must not read as one. Point "
            f"--journal at <graph dir>/events.jsonl (or the graph dir)")
    if given.is_dir():
        graph_dir, segmented = given, True
    else:
        graph_dir = given.parent
        sealed = [p for p in graph_dir.iterdir()
                  if journal_segments.SEALED_RE.match(p.name)]
        segmented = bool(sealed) or (graph_dir / journal_segments.MANIFEST_NAME).exists()

    if segmented:
        records = journal_segments.read_all_segments(graph_dir, salvage=salvage)
        if not records and not (graph_dir / journal_segments.ACTIVE_NAME).exists() \
                and not (graph_dir / journal_segments.MANIFEST_NAME).exists():
            # ⛔ A DIRECTORY IS NOT A JOURNAL BY BEING A DIRECTORY (review P2).
            # With no segment, no manifest and no `events.jsonl` there is no
            # journal here to read — and reading it as ZERO RECORDS makes
            # `apply` print "NOTHING TO DO — every journal position is already in
            # the record", which is the vacuity class this function exists to
            # refuse (the existence branch above cannot see it: the path DOES
            # exist). A real graph directory always carries at least its active
            # journal file or a manifest, so this refuses a mistyped parent
            # without touching a legitimately empty graph.
            raise RunnerError(
                f"journal {given} is a directory with no journal in it — no "
                f"{journal_segments.ACTIVE_NAME}, no sealed segment and no "
                f"{journal_segments.MANIFEST_NAME}. A directory is not a journal "
                f"by being a directory, and reading this as zero records would "
                f"report the record as complete. Point --journal at "
                f"<graph dir>/events.jsonl (or the graph dir)")
        # ⛔ DAMAGE IS REPORTED AS UNKNOWN, NOT AS ZERO (review P2).
        # `read_all_segments` counts a mid-file damage per segment INTERNALLY and
        # then discards the count (it raises or logs, and returns only records) —
        # and it reads every segment through `read_for_replay`, i.e. ALWAYS in
        # salvage mode. So for a segmented layout this tool cannot report the
        # count, and printing `0` would assert "no damage" over a read that had
        # skipped a line and thereby SHIFTED every later record's enumeration.
        # `None` is the only honest value here; the render says "not reported".
        return Journal(path=graph_dir, records=records, segmented=True,
                       salvage=salvage, corrupt=None, torn=None)

    log = EventLog(given)
    records = log.read_all(salvage=salvage)
    return Journal(
        path=given, records=records, segmented=False, salvage=salvage,
        corrupt=int(getattr(log, "corrupt_line_count", 0) or 0),
        torn=int(getattr(log, "torn_trailing_count", 0) or 0),
    )


# ── the record door (read-side only, until a batch is applied) ────────────────


@dataclass(frozen=True)
class Checkpoint:
    """How far the record has consumed this journal, read from the record.

    ``first_gap`` is ``None`` when positions ``1..high`` are all present. A gap
    is a real disagreement — the record is short of a position it should hold —
    and the resume point is the gap, not ``high + 1``.
    """

    high_seq: int = 0
    present: int = 0
    first_gap: int | None = None
    #: The lowest position the record holds (`0` when it holds none). ⛔ THE LOW
    #: EDGE IS NOT OPTIONAL: a record whose rows start ABOVE 1 has consumed none
    #: of the journal's prefix, and a gap query that only looks BETWEEN rows calls
    #: that "contiguous" — see `read_checkpoint`.
    min_seq: int = 0

    @property
    def next_seq(self) -> int:
        return self.first_gap if self.first_gap is not None else self.high_seq + 1

    @property
    def contiguous(self) -> bool:
        return self.first_gap is None


#: One scan, three facts: the high-water mark, the row count, and the LOW edge.
#: ⛔ It no longer computes `lead()` — that column was never read here (the outer
#: query is an aggregate over `seq` alone), so every `status`/`apply` paid a full
#: sort of the graph's rows for a window function whose result was discarded, and
#: `_FIRST_GAP_SQL` then paid the SAME sort again. `min(seq)` costs nothing on the
#: same scan and is a fact the gap decision cannot do without.
_CHECKPOINT_SQL = """
SELECT coalesce(max(seq), 0) AS high,
       count(*) AS present,
       coalesce(min(seq), 0) AS low
FROM public.journal_event
WHERE org_id = %s AND graph_id = %s
"""

_FIRST_GAP_SQL = """
WITH paged AS (
    SELECT seq, lead(seq) OVER (ORDER BY seq) AS nxt
    FROM public.journal_event
    WHERE org_id = %s AND graph_id = %s
)
SELECT min(seq) + 1 AS gap
FROM paged
WHERE nxt IS NOT NULL AND nxt <> seq + 1
"""

_CURSOR_SQL = """
SELECT seq, mode FROM public.record_cursor WHERE org_id = %s AND graph_id = %s
"""


def record_configured(dsn: str | None) -> bool:
    """Is a record DSN configured — asked WITHOUT opening a connection.

    `plan` must not open one (its "writes nothing" property is structural), but
    it still has to tell "no record is armed" from "--from-checkpoint was not
    passed": the first has no remedy in this tool and the second has a one-word
    one, and reporting the first for the second sends an operator to their
    credentials to fix a missing flag.

    A MALFORMED value counts as configured: it is set, so the remedy is to fix
    it, not to set one — and this predicate only chooses a sentence.
    """
    from tortoise.record_writer import record_dsn

    try:
        return bool(record_dsn(dsn))
    except Exception:
        return True


def read_checkpoint(conn: Any, org_id: str, graph_id: str, *,
                    allocated: bool = False) -> Checkpoint:
    """The record's consumed positions — the ledger, read rather than kept.

    ⛔ TWO EDGES, AND THE LOW ONE IS THE DANGEROUS ONE (review P0). The gap query
    can only see a hole that sits BETWEEN two rows, because it looks at a row's
    `lead()` neighbour. A record whose rows start ABOVE position 1 — which is the
    ORDINARY case for this whole lane, not a corner: `public.journal_event.seq`
    carries only `CHECK (seq > 0)`, and the live door writes at the journal's own
    append position, so a graph armed after its journal already held 250 lines
    mints position **251 alone** — produced `first_gap = NULL`, i.e. "contiguous",
    `next_seq = 252`, and `apply` then folded the tail and reported success over a
    record that had never seen records 1..250.

    That is the exact failure this tool exists to prevent, in the exact scenario
    (#7879 hole 2: the historic payload) it exists for, so the low edge is
    checked here rather than left to the caller: `min(seq) > 1` IS a gap, and the
    resume point is position 1.

    ``allocated`` suppresses that check, and only for one reason: on a graph whose
    ordinals are ISSUED (`public.record_cursor` row), the first position is the
    allocator's seed floor, not 1 — so a legitimate record that starts at 500 is
    not short of anything. The caller states which arm it is on; this function
    does not guess.
    """
    from tortoise.record_projection import scope_connection

    cursor = scope_connection(conn, org_id)
    cursor.execute(_CHECKPOINT_SQL, (org_id, graph_id))
    row = cursor.fetchone()
    if not row:
        raise RunnerError(
            "the record's checkpoint read returned no row — an aggregate over "
            "an empty set always returns one, so this is not 'empty', it is "
            "unreadable; refusing rather than reporting 0")
    if isinstance(row, Mapping):
        high, present, low = row["high"], row["present"], row["low"]
    else:
        high, present, low = row[0], row[1], row[2]
    high, present, low = int(high or 0), int(present or 0), int(low or 0)
    if present == 0:
        return Checkpoint(high_seq=0, present=0, first_gap=None, min_seq=0)
    cursor.execute(_FIRST_GAP_SQL, (org_id, graph_id))
    gap_row = cursor.fetchone()
    gap = None
    if gap_row:
        raw = gap_row["gap"] if isinstance(gap_row, Mapping) else gap_row[0]
        gap = int(raw) if raw is not None else None
    if gap is None and not allocated and low > 1:
        # The record holds rows, and the lowest is above 1: the journal's PREFIX
        # was never folded. Resume at 1 — the unconsumed prefix is the whole job.
        gap = 1
    return Checkpoint(high_seq=high, present=present, first_gap=gap,
                      min_seq=low)


def read_cursor_state(conn: Any, org_id: str, graph_id: str,
                      ) -> tuple[int, str] | None:
    """``(seq, mode)`` when the graph is cut over, ``None`` when it is not.

    Only ever a READ. ``replay()`` performs the authoritative refusal
    (``_refuse_an_enumerated_replay``, #9190); this is here so the report can name
    the state before a write is attempted, not so this file can decide it.
    """
    from tortoise.record_projection import scope_connection

    cursor = scope_connection(conn, org_id)
    cursor.execute(_CURSOR_SQL, (org_id, graph_id))
    row = cursor.fetchone()
    if row is None:
        return None
    seq, mode = ((row["seq"], row["mode"]) if isinstance(row, Mapping)
                 else (row[0], row[1]))
    return int(seq), str(mode)


def open_record(org_id: str, *, dsn: str | None, timeout_ms: int) -> Any:
    """Open the record on the ONE declared arming surface, with a bounded batch.

    The DSN is resolved by :func:`tortoise.record_writer.record_dsn` — the module
    that DECLARES the surface — so this door cannot drift from the writer's notion
    of "armed". ``autocommit`` is left alone: the batch transaction is this
    tool's, and ``replay()`` REFUSES an autocommit connection at its first
    statement (the tenant GUC read-back), so turning it on here would fail late
    and confusingly instead of never.
    """
    from tortoise.record_writer import _default_connect, record_dsn

    resolved = record_dsn(dsn)
    if not resolved:
        raise RunnerError(
            "no record DSN — pass --dsn or arm TORTOISE_RECORD_DSN. The fold "
            "writes to the record, so an unarmed run has no destination")
    conn = _default_connect(resolved)
    with conn.cursor() as cur:
        # Transaction-local: the handle may be POOLED (#9558), and a session-level
        # SET would impose this run's backfill bound on the next borrower.
        cur.execute("SELECT set_config('statement_timeout', %s, true)",
                    (str(int(timeout_ms)),))
    return conn


# ── planning ─────────────────────────────────────────────────────────────────


@dataclass
class BatchPlan:
    """One batch as the MODULE planned it. Pure: nothing here has been executed.

    ``statements`` counts the RECORD MUTATIONS only, which is the quantity
    :class:`tortoise.record_projection.ReplayReport` reports — the position row
    is excluded from both, so an operator can compare a plan to a run and a
    disagreement means something. ``position_rows`` is the excluded half, stated
    rather than silently dropped: on the ENUMERATED arm it is one position row
    per record, and it IS written.

    ⛔ IT COUNTS STATEMENTS THAT WRITE POSITIONS, NOT ROWS (review note). On the
    ALLOCATE arm every position write travels INSIDE the single
    ``ALLOCATION_STATEMENT_PREFIX`` statement (N rows in 1 statement), so this
    reads ``1`` for a batch of N — which
    ``test_plan_allocate_accounts_the_allocation_statement_as_POSITIONS`` pins as
    intended ("an allocation batch is ONE position write"). Nothing writable is
    misled by the coarser unit: only ``plan`` can reach this count, because
    ``apply --allocate`` is refused. Read it as a statement-class count, never as
    a census of rows.
    """

    start_seq: int
    records: int
    statements: int
    events: dict[str, int]
    position_rows: int = 0

    @property
    def end_seq(self) -> int:
        return self.start_seq + self.records - 1


def plan_batch(records: Sequence[Mapping[str, Any]], *, start_seq: int,
               org_id: str, graph_id: str, allocate: bool = False) -> BatchPlan:
    """Plan ONE batch through :func:`replay_plan` — the totality gate, dry.

    ``replay_plan`` raises on a record type the registry does not declare, which
    is `#5285`'s rule reaching this tool: an event with no handler is a failure,
    not a skip. Planning therefore *finds* that before any write, which is the
    whole reason the dry run exists.

    ``carried_positions`` is deliberately left at its default (``False``): these
    records come from the journal, so the ordinal is the enumeration from
    ``start_seq``. That is `#13043`'s own rule — the flag is a claim about where
    the batch CAME FROM, and a journal read is the shape the default describes.
    """
    from tortoise.record_projection import (
        _JOURNAL_INSERT_PREFIX,
        ALLOCATION_STATEMENT_PREFIX,
        replay_plan,
    )

    planned = replay_plan(records, org_id=org_id, graph_id=graph_id,
                          start_seq=start_seq, allocate=allocate)
    # ⛔ THE SAME COUNT `ReplayReport.statements` MAKES. It skips the journal
    # position row (`_JOURNAL_INSERT_PREFIX`) because that row is the record's
    # CLAIM about what it consumed, not a projection mutation — so counting it
    # here would make every plan read one statement per record HIGHER than the
    # run it describes, and an operator comparing the two would see a
    # disagreement that is not one. The count is imported, not re-spelled, so the
    # two cannot drift.
    # ⛔ THREE CLASSES, NOT TWO (review P2). The ALLOCATION statement also writes
    # journal positions (`INSERT INTO public.journal_event` is INSIDE it), which
    # is exactly the `startswith` collision `#9318` paid for — but its SQL begins
    # `WITH payloads AS (`, so `_JOURNAL_INSERT_PREFIX` does NOT match it and it
    # was counted as a MUTATION while all its position writes were counted as
    # none. On the allocate arm that read `1 statements, 0 position rows` for a
    # batch of 3 that writes 3 rows — and `ReplayReport` skips the allocation
    # prefix too, so plan and run disagreed by one per batch, which is the drift
    # this docstring claims is impossible.
    mutations = sum(1 for st in planned
                    if not st.sql.startswith(_JOURNAL_INSERT_PREFIX)
                    and not st.sql.startswith(ALLOCATION_STATEMENT_PREFIX))
    position_rows = len(planned) - mutations
    return BatchPlan(start_seq=start_seq, records=len(records),
                     statements=mutations, position_rows=position_rows,
                     events=dict(Counter(str(r.get("type")) for r in records)))


def batch_slices(total: int, *, start_at: int = 0, batch_size: int,
                 limit: int | None = None) -> list[tuple[int, int]]:
    """``[(offset, length), …]`` — bounded windows over ``records[start_at:]``.

    ``offset`` is an index into the journal list, so the batch's journal position
    is always ``offset + 1`` and the two can never be computed twice.
    """
    if batch_size < 1:
        raise RunnerError(f"--batch-size must be >= 1 (got {batch_size!r})")
    if limit is not None and limit < 1:
        raise RunnerError(f"--limit must be >= 1 (got {limit!r})")
    out: list[tuple[int, int]] = []
    remaining = total - start_at
    if remaining < 0:
        raise RunnerError(
            f"the resume point {start_at} is past the end of the journal "
            f"({total} records) — the record holds positions this journal does "
            f"not have, so they are not the same journal. Refusing to fold a "
            f"short journal over a longer record")
    if limit is not None:
        remaining = min(remaining, limit)
    while remaining > 0:
        take = min(batch_size, remaining)
        out.append((start_at, take))
        start_at += take
        remaining -= take
    return out


# ── the verbs ────────────────────────────────────────────────────────────────


@dataclass
class WriteLedger:
    """What THIS RUN executed against the record — the plan's falsifier, measured.

    ``plan`` opens no connection, so its ledger stays empty; the zeros it reports
    are READ FROM HERE rather than printed as literals. That distinction is the
    whole point (review P2): an expected value that is a constant outside the
    code under test cannot be perturbed by any mutant of the write path, so an
    assertion on it is true even for a `plan` that wrote rows. ``apply`` fills the
    same type from the fold's own report, so the counter is proven reachable.
    """

    rows_written: int = 0
    statements_executed: int = 0


@dataclass
class PlanReport:
    org_id: str
    graph_id: str
    journal: dict[str, Any]
    checkpoint: dict[str, Any] | None
    cursor: dict[str, Any] | None
    start_at: int
    events_to_consume: int
    batches: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    #: True when the run was GIVEN a record (a DSN), whether or not it read one.
    record_armed: bool = False
    #: The run's own write accounting. A planner never touches it; that it is
    #: empty is a MEASUREMENT, and a renderer that ignored it would be caught.
    executed: WriteLedger = field(default_factory=WriteLedger)

    def as_dict(self) -> dict[str, Any]:
        return {
            "verb": "plan",
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "journal": self.journal,
            "checkpoint": self.checkpoint,
            "cursor": self.cursor,
            "start_at": self.start_at,
            "events_to_consume": self.events_to_consume,
            "batches": self.batches,
            "totals": self.totals,
            # The falsifier, MEASURED by the tool's own accounting rather than
            # printed as a constant — see `WriteLedger`.
            "rows_written": self.executed.rows_written,
            "statements_executed": self.executed.statements_executed,
        }


def build_plan(*, journal: Journal, org_id: str, graph_id: str,
               start_at: int, batch_size: int, limit: int | None,
               allocate: bool) -> PlanReport:
    """Plan the run WITHOUT writing. The number it reports is what ``apply`` wants."""
    slices = batch_slices(len(journal.records), start_at=start_at,
                          batch_size=batch_size, limit=limit)
    totals_types: Counter[str] = Counter()
    batches: list[dict[str, Any]] = []
    statements_total = 0
    positions_total = 0
    events_total = 0
    for offset, length in slices:
        batch = journal.records[offset:offset + length]
        # ⛔ ON THE ALLOCATION ARM THE JOURNAL POSITION IS NOT THIS TOOL'S TO
        # STATE (review P1). `replay_plan` refuses `allocate=True` with
        # `start_seq != 1` by name — the counter issues the number — so passing
        # `offset + 1` made every batch after the first refuse, and `apply`
        # refused AFTER committing batch 1 (a partial write reported as "nothing
        # was written"). `1` is what the module asks for and what the live door
        # passes; the allotted range is read back from the fold's own report.
        planned = plan_batch(batch, start_seq=(1 if allocate else offset + 1),
                             org_id=org_id, graph_id=graph_id, allocate=allocate)
        statements_total += planned.statements
        positions_total += planned.position_rows
        events_total += planned.records
        totals_types.update(planned.events)
        batches.append({
            "start_seq": planned.start_seq,
            "end_seq": planned.end_seq,
            "records": planned.records,
            "statements": planned.statements,
            "position_rows": planned.position_rows,
            "events": planned.events,
        })
    return PlanReport(
        org_id=org_id, graph_id=graph_id, start_at=start_at,
        events_to_consume=events_total, batches=batches,
        journal={"path": str(journal.path), "records": len(journal.records),
                 "segmented": journal.segmented, "salvage": journal.salvage,
                 "corrupt_lines": journal.corrupt, "torn_tails": journal.torn},
        checkpoint=None, cursor=None,
        totals={"events": events_total, "statements": statements_total,
                "position_rows": positions_total,
                "events_by_type": dict(sorted(totals_types.items()))},
    )


@dataclass
class ApplyReport:
    org_id: str
    graph_id: str
    journal: dict[str, Any]
    checkpoint_before: dict[str, Any]
    start_at: int
    consumed: int = 0
    statements: int = 0
    batches: int = 0
    committed: int = 0
    first_seq: int = 0
    last_seq: int = 0
    checkpoint_after: dict[str, Any] | None = None
    events_by_type: dict[str, int] = field(default_factory=dict)
    #: Filled from the fold's own report on every COMMITTED batch — the same
    #: counter a plan reports as empty, so its zero is a measurement.
    executed: WriteLedger = field(default_factory=WriteLedger)

    @property
    def wrote(self) -> bool:
        return self.consumed > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "verb": "apply",
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "journal": self.journal,
            "checkpoint_before": self.checkpoint_before,
            "start_at": self.start_at,
            "consumed": self.consumed,
            "statements": self.statements,
            "batches": self.batches,
            "batches_committed": self.committed,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "checkpoint_after": self.checkpoint_after,
            "events_by_type": self.events_by_type,
            # One sentence the falsifier can quote: a no-op says so.
            "no_op": not self.wrote,
        }


def apply_batches(*, journal: Journal, org_id: str, graph_id: str,
                  conn: Any, start_at: int, batch_size: int,
                  limit: int | None, allocate: bool,
                  timeout_ms: int, echo) -> ApplyReport:
    """Fold the journal in bounded batches, one commit each.

    A batch that raises is ROLLED BACK and the run stops: the transaction is the
    batch, so the record never holds half of one. A COMMIT that raises is
    reported as a refusal rather than counted, because a commit whose outcome is
    unknown is not a landing — the batch will be re-derived from the record's
    checkpoint on the next run, which is exactly what the derived checkpoint is
    for.
    """
    from tortoise.record_projection import replay

    slices = batch_slices(len(journal.records), start_at=start_at,
                          batch_size=batch_size, limit=limit)
    report = ApplyReport(
        org_id=org_id, graph_id=graph_id, start_at=start_at,
        journal={"path": str(journal.path), "records": len(journal.records),
                 "segmented": journal.segmented, "salvage": journal.salvage,
                 "corrupt_lines": journal.corrupt, "torn_tails": journal.torn},
        checkpoint_before={},
    )
    types: Counter[str] = Counter()
    for index, (offset, length) in enumerate(slices, start=1):
        batch = journal.records[offset:offset + length]
        start_seq = offset + 1
        # ⛔ THE PLAN PRECEDES THE WRITE, per batch, as well as per run. This is
        # the module's own totality gate reached a second way: `replay` plans the
        # whole batch before executing any of it, and printing that plan first
        # means an operator watching a long backfill sees the batch it is about
        # to apply rather than one it already applied.
        start_seq = 1 if allocate else offset + 1
        planned = plan_batch(batch, start_seq=start_seq, org_id=org_id,
                             graph_id=graph_id, allocate=allocate)
        echo(f"batch {index}/{len(slices)}: seq {planned.start_seq}.."
             f"{planned.end_seq}, {planned.records} events → "
             f"{planned.statements} statements + {planned.position_rows} "
             f"position row(s) (plan)")
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT set_config('statement_timeout', %s, true)",
                            (str(int(timeout_ms)),))
            result = replay(batch, conn, org_id=org_id, graph_id=graph_id,
                            start_seq=start_seq, allocate=allocate)
        except Exception as exc:
            # The MUTATION failed: roll back and claim nothing. This is the one
            # case where "nothing from this batch is in the record" is TRUE.
            _quietly(conn.rollback)
            report.events_by_type = dict(sorted(types.items()))
            raise RunnerError(
                f"batch {index}/{len(slices)} (seq {planned.start_seq}.."
                f"{planned.end_seq}) failed and was ROLLED BACK: "
                f"{_one_line(exc)} — nothing from THIS batch is in the record. "
                f"The checkpoint is derived from the record, so re-running "
                f"resumes at the position the record actually holds"
            ) from exc
        try:
            conn.commit()
        except Exception as exc:
            # ⛔ A COMMIT WHOSE OUTCOME IS UNKNOWN IS NOT A ROLLBACK (review P2).
            # The server may have committed before the round trip was lost, so
            # telling the operator "nothing landed" would be a lie in exactly the
            # case that matters. Say what is known and let the derived checkpoint
            # decide on the next run — which is what it is for.
            report.events_by_type = dict(sorted(types.items()))
            raise RunnerError(
                f"batch {index}/{len(slices)} (seq {planned.start_seq}.."
                f"{planned.end_seq}): the COMMIT's outcome is UNKNOWN "
                f"({_one_line(exc)}). It may be durable. The checkpoint is "
                f"derived from the record, so re-running decides from what the "
                f"record actually holds rather than from this message"
            ) from exc
        report.batches += 1
        report.committed += 1
        report.consumed += result.events
        report.statements += result.statements
        report.executed.rows_written += result.events
        report.executed.statements_executed += result.statements
        types.update(planned.events)
        # ⛔ THE ALLOTTED RANGE COMES FROM THE FOLD (review P1): on the allocation
        # arm the numbers are the COUNTER's, and `ReplayReport` reports them
        # (`first_seq`/`last_seq`, populated from the allocated block). Printing
        # the plan's enumerated numbers there would name positions nothing wrote.
        first = getattr(result, "first_seq", 0) or planned.start_seq
        last = getattr(result, "last_seq", 0) or planned.end_seq
        if not report.first_seq:
            report.first_seq = first
        report.last_seq = last
        echo(f"batch {index}/{len(slices)}: committed — {result.events} events, "
             f"{result.statements} statements, seq {first}..{last}")
    report.events_by_type = dict(sorted(types.items()))
    return report


def _one_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return text[0] if text else type(exc).__name__


def _quietly(fn) -> None:
    """Run a disposal step whose failure must not mask the cause above it."""
    with contextlib.suppress(Exception):
        fn()


# ── output ───────────────────────────────────────────────────────────────────


def _render_plan(report: PlanReport) -> str:
    j = report.journal
    lines = [
        f"journal: {j['path']} — {j['records']} records"
        f" ({'segmented' if j['segmented'] else 'single file'}"
        f"{', SALVAGE' if j['salvage'] else ''})",
    ]
    if j["corrupt_lines"] is None or j["torn_tails"] is None:
        lines.append("  ⚠ damage NOT REPORTED: this layout's reader does not "
                     "return its damage counters, and it reads every segment in "
                     "SALVAGE mode — so a skipped mid-file line would have "
                     "SHIFTED the enumeration and this run cannot see it")
    elif j["corrupt_lines"] or j["torn_tails"]:
        lines.append(f"  ⚠ damaged input: {j['corrupt_lines']} mid-file line(s), "
                     f"{j['torn_tails']} torn tail fragment(s) — a SKIPPED line "
                     f"SHIFTS every later record's position, so this is NOT a "
                     f"completeness claim")
    if report.checkpoint is None:
        # ⛔ TWO DIFFERENT FACTS, and telling them apart is the point: "no record
        # is armed" has no remedy in this tool, while "you did not ask" has a
        # one-word one. Reporting the first for the second sends an operator to
        # their DSN to fix a missing flag.
        if report.record_armed:
            lines.append("checkpoint: NOT READ (--from-checkpoint was not passed) "
                         "— planning from seq 1; the record may already hold some "
                         "of this, so `apply` will ask for a DIFFERENT number. "
                         "Pass --from-checkpoint to plan what a run would do")
        else:
            lines.append("checkpoint: UNREAD (no record armed) — planning from "
                         "seq 1; the record may already hold some of this")
    else:
        c = report.checkpoint
        lines.append(f"checkpoint: present {c['present']}, high {c['high_seq']}, "
                     f"first gap {c['first_gap']} → resume at seq {c['next_seq']}")
    if report.cursor is not None:
        lines.append(f"cutover: cursor present (seq {report.cursor['seq']}, "
                     f"mode {report.cursor['mode']}) — an enumerated replay is "
                     f"REFUSED on this graph; the fold does not apply")
    lines.append(f"events to consume: {report.events_to_consume}  "
                 f"(in {len(report.batches)} batch(es))")
    for index, b in enumerate(report.batches, start=1):
        detail = ", ".join(f"{k}={v}" for k, v in sorted(b["events"].items()))
        lines.append(f"  batch {index}: seq {b['start_seq']}..{b['end_seq']} — "
                     f"{b['records']} events, {b['statements']} statements, "
                     f"{b['position_rows']} position row(s)"
                     + (f" [{detail}]" if detail else ""))
    t = report.totals
    lines.append(f"TOTAL: {t['events']} events → {t['statements']} statements + "
                 f"{t['position_rows']} position row(s) (the position rows are the "
                 f"record's own ledger; `statements` is comparable to a run's report)")
    lines.append(f"rows_written: {report.executed.rows_written} · "
                 f"statements_executed: {report.executed.statements_executed}  "
                 "(the plan path opens no connection unless asked for the checkpoint)")
    if report.events_to_consume:
        lines.append("")
        if report.cursor is not None:
            # ⛔ DON'T HAND OVER A NUMBER ON AN ARM THAT CANNOT WRITE (review P2).
            # The cutover verdict above already says an enumerated replay is
            # REFUSED here, so this number is one `apply` can only answer with
            # that refusal — the same defect this block fixes for the arm that
            # never read the record, one line below.
            lines.append("no acknowledgement is handed over: this graph is CUT "
                         "OVER, so `apply` refuses the enumerated replay")
        elif report.checkpoint is not None:
            lines.append("to apply this plan:  --acknowledge-plan "
                         f"{report.events_to_consume}")
        else:
            # ⛔ DON'T PRINT AN INSTRUCTION `apply` WILL REJECT (review P2). This
            # number was planned from seq 1 without reading the record, so on any
            # non-empty record `apply` computes a DIFFERENT one and refuses. The
            # tool used to close with the number and thereby hand the operator a
            # guaranteed refusal.
            lines.append("this plan did NOT read the record, so its number is not "
                         "the one `apply` will ask for. Run "
                         "`record_runner plan --from-checkpoint …` for the number "
                         "to acknowledge")
    return "\n".join(lines)


def _render_apply(report: ApplyReport) -> str:
    lines = [
        f"journal: {report.journal['path']} — {report.journal['records']} records",
        f"resumed at seq: {report.start_at + 1} "
        f"(checkpoint before: {report.checkpoint_before})",
    ]
    if not report.wrote:
        lines.append("NOTHING TO DO — every journal position is already in the "
                     "record. consumed=0, statements=0, batches=0. A second run "
                     "is a no-op by the record's own ledger.")
    else:
        lines.append(f"consumed: {report.consumed} events in {report.batches} "
                     f"batch(es), {report.statements} statements, "
                     f"seq {report.first_seq}..{report.last_seq}")
        lines.append(f"committed batches: {report.committed}")
    if report.checkpoint_after is not None:
        c = report.checkpoint_after
        lines.append(f"checkpoint after: present {c['present']}, high "
                     f"{c['high_seq']}, first gap {c['first_gap']} → next run "
                     f"resumes at seq {c['next_seq']}")
    lines.append("graph: untouched — this tool opens no graph connection and "
                 "issues no delete, prune or Cypher of any kind")
    return "\n".join(lines)


def emit(payload: Mapping[str, Any], text: str, *, as_json: bool) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(text + "\n")


# ── CLI ──────────────────────────────────────────────────────────────────────


class _UsageParser(argparse.ArgumentParser):
    """argparse's exit 2 is USAGE here — so a typo and a refusal are different."""

    def error(self, message):  # pragma: no cover - exercised via a subprocess
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{self.prog}: error: {message}\n")
        raise SystemExit(EXIT_USAGE)


def _parser() -> argparse.ArgumentParser:
    ap = _UsageParser(
        description="Run the record fold: journal → Postgres (epic #7879). "
                    "It does not touch the graph.")
    sub = ap.add_subparsers(dest="verb", required=True)

    def common(p: argparse.ArgumentParser, *, bounds: bool = True) -> None:
        p.add_argument("--org-id", required=True)
        p.add_argument("--graph-id", required=True)
        p.add_argument("--journal", required=True,
                       help="the graph's events.jsonl, or its graph directory")
        p.add_argument("--dsn", default=None,
                       help="the record DSN (default: TORTOISE_RECORD_DSN)")
        # ⛔ THE BOUNDS ARE NOT `status`'S (review note, PR #13210). `status` runs
        # no batch and consumes nothing, so `--limit` and `--batch-size` were
        # ACCEPTED there and silently IGNORED: an operator passing
        # `status --limit 5` got the full remainder with no indication the flag
        # did nothing. A flag that cannot do anything is worse than an absent
        # one, so `status` does not register them and passing one is a usage
        # error (exit 2) — the honest answer to "what does this flag do here?".
        #
        # ⛔ AND `--allocate` IS THE SAME DEFECT (review P2 at `b7a5207b93`): it
        # was registered HERE, outside this block, so `status --allocate` exited 0
        # and printed output byte-identical to the run without it. `status` does
        # not take the flag's word for anything — it reads the `record_cursor`
        # row and derives `allocated` from that fact, which is `#9190`'s point
        # that the ROW is the fact and the mode is not. So it belongs with the run
        # verbs that actually plan a batch (`plan` uses it; `apply` registers it
        # only to REFUSE it by name, which is a better answer than "unrecognised
        # arguments"), not with `status`.
        if bounds:
            p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                           dest="batch_size")
            p.add_argument("--limit", type=int, default=None,
                           help="consume at most N events this run")
            p.add_argument("--allocate", action="store_true",
                           help="use the ordinal ALLOCATOR (a cut-over graph) "
                                "instead of the journal's enumeration")
        p.add_argument("--salvage", action="store_true",
                       help="read a damaged journal, skipping mid-file damage "
                            "(explicitly NOT a completeness claim)")
        p.add_argument("--timeout-ms", type=int,
                       default=DEFAULT_STATEMENT_TIMEOUT_MS, dest="timeout_ms")
        p.add_argument("--json", action="store_true", dest="as_json")

    p_plan = sub.add_parser("plan", help="produce the plan; writes nothing")
    common(p_plan)
    p_plan.add_argument("--from-checkpoint", action="store_true",
                        dest="from_checkpoint",
                        help="read the record to plan only what remains (opens a "
                             "READ-ONLY connection)")

    p_apply = sub.add_parser("apply", help="fold the journal into the record")
    common(p_apply)
    p_apply.add_argument("--acknowledge-plan", type=int, default=None,
                         dest="acknowledge_plan",
                         help="the number of events the `plan` output reported. "
                              "Required for a run that would write: you cannot "
                              "acknowledge a number you have not seen")

    p_status = sub.add_parser("status", help="where the fold stands; writes nothing")
    common(p_status, bounds=False)

    return ap


def _refuse_impossible_bounds(args: argparse.Namespace) -> None:
    """A bound below 1 is an ARGUMENT error, refused BEFORE any read (review P1).

    ⛔ THE GUARD IN :func:`batch_slices` IS NOT ENOUGH, because it is reachable
    only from a path that actually computes a window. `apply` took its "nothing
    to consume" arm BEFORE the slicer was ever called, so `apply --limit 0`
    printed `NOTHING TO DO — every journal position is already in the record`
    over an UNTOUCHED record — the same report said `present 0` two lines above
    and `next run resumes at seq 1` two lines below — and exited 0, while `plan`
    refused the identical input with `--limit must be >= 1`.

    Two facts had been merged and they must not be: an invalid bound is an
    ARGUMENT failure (rc 2, nothing read, nothing claimed), and "nothing to do"
    is a VERIFIED statement about the remainder. A scripted backfill reads
    `rc=0` plus that sentence as *complete*, so the merge is a false completion
    claim rather than a cosmetic one. The bound describes the INVOCATION, so it
    is checked from the parsed arguments — before the journal is read, before a
    connection is opened, and on every code path the verb could take.

    ⛔ THE CODE IS 2 (USAGE), NOT 1 (REFUSED) — review P2. `1` is reserved for
    "REFUSED by the record: the graph is unchanged, read the reason, do not
    re-run blindly" (see the module's EXIT CODES and
    `record_allocator_cli:54-59`), and the #5128 guard added above argues the
    same doctrine about the interpreter. A typo in the caller's own flags is not
    a claim about the record, and a backfill chain that reads `1` as "the record
    said no" stops work on a healthy store.
    """
    for flag, value in (("--batch-size", args.batch_size),
                        ("--limit", args.limit)):
        if value is not None and value < 1:
            sys.stderr.write(
                f"record_runner: {flag} must be >= 1 (got {value!r}) — this is "
                f"a usage error (exit 2), not a refusal by the record: "
                f"nothing was read and nothing was written\n")
            raise SystemExit(EXIT_USAGE)


def cmd_plan(args: argparse.Namespace, *, echo) -> int:
    _refuse_impossible_bounds(args)
    journal = read_journal(args.journal, salvage=args.salvage)
    start_at = 0
    checkpoint = None
    cursor = None
    if args.from_checkpoint:
        conn = open_record(args.org_id, dsn=args.dsn, timeout_ms=args.timeout_ms)
        try:
            # ⛔ THE CURSOR IS READ FIRST, because the CHECKPOINT'S MEANING
            # depends on it: on an allocated graph the first position is the
            # allocator's seed floor, so `min(seq) > 1` is NOT a gap. Reading the
            # checkpoint first and passing `allocated=False` would report a
            # phantom unconsumed prefix on every correctly-seeded graph.
            cursor_state = read_cursor_state(conn, args.org_id, args.graph_id)
            cp = read_checkpoint(conn, args.org_id, args.graph_id,
                                 allocated=cursor_state is not None)
            # ⛔ THE CURSOR IS READ HERE TOO (review P2). `report.cursor` used to
            # be hardcoded `None`, so the `cutover:` line and the JSON field were
            # UNREACHABLE from the CLI — this tool would hand an operator an
            # `events to consume` count and an acknowledgement number for a graph
            # whose only possible outcome is `apply` refusing with CUT OVER.
            cursor = cursor_state
            conn.rollback()   # read-only: nothing was written, so nothing commits
        finally:
            _quietly(conn.close)
        checkpoint = {"high_seq": cp.high_seq, "present": cp.present,
                      "first_gap": cp.first_gap, "next_seq": cp.next_seq,
                      "min_seq": cp.min_seq, "contiguous": cp.contiguous}
        start_at = max(0, cp.next_seq - 1)
        # ⛔ THIS IS A JOURNAL-COORDINATE CHECK, so it may not run on the ALLOCATED
        # arm: there `high_seq` is an ordinal the counter issued, not an index
        # into this journal, and comparing the two refused a correctly-seeded
        # graph with "they are not the same journal" — a wrong diagnosis of a
        # healthy store (the coordinate confusion behind review P1.3).
        if cursor is None and cp.next_seq - 1 > len(journal.records):
            raise RunnerError(
                f"the record holds positions up to {cp.high_seq} but the journal "
                f"has only {len(journal.records)} records — they are not the same "
                f"journal. Refusing to plan a fold over a shorter one")
        if cursor is not None:
            # ⛔ ON THE ALLOCATED ARM THE CHECKPOINT IS NOT IN JOURNAL
            # COORDINATES, so it cannot position a journal plan at all: `high_seq`
            # is an ordinal the counter issued. Planning from it made
            # `batch_slices` refuse a correctly-seeded graph with "they are not
            # the same journal". The plan therefore describes the WHOLE journal,
            # and the printed `cutover:` line carries the verdict that matters —
            # an enumerated fold does not apply to this graph.
            start_at = 0
    report = build_plan(journal=journal, org_id=args.org_id,
                        graph_id=args.graph_id, start_at=start_at,
                        batch_size=args.batch_size, limit=args.limit,
                        allocate=args.allocate)
    report.checkpoint = checkpoint
    report.cursor = None if cursor is None else {"seq": cursor[0], "mode": cursor[1]}
    report.record_armed = record_configured(args.dsn)
    emit(report.as_dict(), _render_plan(report), as_json=args.as_json)
    return EXIT_OK


def cmd_status(args: argparse.Namespace, *, echo) -> int:
    journal = read_journal(args.journal, salvage=args.salvage)
    conn = open_record(args.org_id, dsn=args.dsn, timeout_ms=args.timeout_ms)
    try:
        cursor = read_cursor_state(conn, args.org_id, args.graph_id)
        cp = read_checkpoint(conn, args.org_id, args.graph_id,
                             allocated=cursor is not None)
        conn.rollback()       # read-only: nothing was written, so nothing commits
    finally:
        _quietly(conn.close)
    allocated = cursor is not None
    # ⛔ NEVER SUBTRACT TWO COORDINATE SYSTEMS (review P1). On a cut-over graph
    # `journal_event.seq` is an ordinal the COUNTER issued, not an index into
    # this journal, and `cp.next_seq` is in THAT coordinate — so
    # `len(journal) - (next_seq - 1)` subtracted an allocator ordinal from a
    # journal length and reported `remaining: 0` / `fully_consumed: true` for a
    # record holding 3 ordinals and NONE of this journal's records. The
    # remainder is only a number on the enumerated arm; on the allocated arm the
    # honest answer is that the fold does not apply, which `plan` already says.
    #
    # ⛔ AND THE ENUMERATED ARM HAS THE SAME HOLE ONE STEP WIDER (review P2): a
    # record whose positions run PAST this journal's length is a DIFFERENT
    # journal — `plan` refuses that input by name — and clamping the subtraction
    # with `max(0, …)` turned it into a completion claim. Two stores holding
    # different histories must not be reconciled into "done" by arithmetic.
    mismatch = None
    if allocated:
        remaining = None
    elif cp.next_seq - 1 > len(journal.records):
        remaining = None
        mismatch = (f"the record holds positions up to {cp.high_seq} but "
                    f"the journal has only {len(journal.records)} records — "
                    f"they are not the same journal")
    else:
        remaining = len(journal.records) - (cp.next_seq - 1)
    payload = {
        "verb": "status",
        "org_id": args.org_id,
        "graph_id": args.graph_id,
        "journal": {"path": str(journal.path), "records": len(journal.records),
                    "segmented": journal.segmented},
        "checkpoint": {"high_seq": cp.high_seq, "present": cp.present,
                       "first_gap": cp.first_gap, "next_seq": cp.next_seq,
                       "min_seq": cp.min_seq, "contiguous": cp.contiguous},
        "cursor": None if cursor is None else {"seq": cursor[0], "mode": cursor[1]},
        "events_remaining": None if remaining is None else max(0, remaining),
        "fully_consumed": (None if remaining is None
                           else (remaining <= 0 and cp.contiguous)),
    }
    text = "\n".join([
        f"journal: {journal.path} — {len(journal.records)} records",
        f"record: present {cp.present}, high {cp.high_seq}, first gap "
        f"{cp.first_gap} → resume at seq {cp.next_seq}",
        ("events remaining: unknown — this graph is CUT OVER, so its record's "
         "positions are the allocator's and not this journal's; the fold does "
         "not apply" if allocated
         else f"events remaining: unknown — {mismatch}" if mismatch is not None
         else f"events remaining: {max(0, remaining)}"),
        "cutover: " + ("none (positions are the journal's)" if cursor is None
                       else f"cursor present (seq {cursor[0]}, mode {cursor[1]})"),
    ])
    emit(payload, text, as_json=args.as_json)
    return EXIT_OK


def cmd_apply(args: argparse.Namespace, *, echo) -> int:
    # ⛔ BEFORE EVERY OTHER GATE, because it is not a gate about this RECORD:
    # an impossible bound makes the invocation itself meaningless, so it is
    # refused before the journal is read and before `consumable` — the value the
    # `--limit 0` bug turned into a completion claim — is ever computed.
    _refuse_impossible_bounds(args)
    if args.salvage:
        # ⛔ A WRITE MUST NOT BE POSITIONED FROM A READ THAT SKIPPED A LINE. A
        # mid-file damaged line is skipped in salvage mode, which SHIFTS the
        # enumeration of every later record — and the position written here is
        # durable and is the checkpoint's key. `plan` may salvage (it writes
        # nothing and the count is informational); a fold may not.
        raise RunnerError(
            "apply refuses --salvage: a skipped mid-file line SHIFTS every later "
            "record's position, and the position this tool writes is durable and "
            "is what the next run resumes from. Repair the journal, or use "
            "`plan --salvage` to see what parses")
    if args.allocate:
        # ⛔ REFUSED, NOT BROKEN-BY-DESIGN (review P1). The allocation arm issues
        # FRESH ordinals from `public.record_cursor` — it does not carry the
        # journal's — so a HISTORIC fold through it would mint new positions for
        # old events (inflating the counter and leaving the enumerated checkpoint
        # with nothing to resume from: position N in the record no longer means
        # "record N of the journal"). A cut-over graph's history is the
        # allocator's SEED FLOOR's business, not a replay's.
        raise RunnerError(
            "apply refuses --allocate: the counter issues FRESH positions, so a "
            "historic fold through it mints new ordinals for old events and "
            "leaves the derived checkpoint with nothing to resume from. A "
            "cut-over graph's history belongs to the allocator's seed floor "
            "(`python -m tortoise record-allocator seed --journal …`), and live "
            "writes are the allocator's (`arm`). `plan --allocate` is still "
            "available: it writes nothing")
    journal = read_journal(args.journal, salvage=args.salvage)
    conn = open_record(args.org_id, dsn=args.dsn, timeout_ms=args.timeout_ms)
    try:
        cursor = read_cursor_state(conn, args.org_id, args.graph_id)
        cp = read_checkpoint(conn, args.org_id, args.graph_id,
                             allocated=cursor is not None)
        conn.rollback()       # read-only: nothing was written, so nothing commits
        start_at = max(0, cp.next_seq - 1)
        # ⛔ THE CUT-OVER VERDICT COMES FIRST (review P1.3). On an allocated graph
        # the record's positions are the COUNTER's, so the length comparison below
        # is between two different coordinates — and it fired first, telling the
        # operator "they are not the same journal" about a healthy cut-over store
        # whose real state is "the fold does not apply here".
        if cursor is not None and not args.allocate:
            raise RunnerError(
                f"this graph is CUT OVER (record_cursor seq={cursor[0]}, "
                f"mode={cursor[1]!r}): its positions are ISSUED, so an enumerated "
                f"replay would store the same journal event at a second position "
                f"with no error (#9190). The fold does not apply here — the "
                f"remedy belongs to the allocator, and it is NOT --allocate: a "
                f"cut-over graph's history is its SEED FLOOR's business "
                f"(`python -m tortoise record-allocator seed --journal …`), and "
                f"`--allocate` is refused on a write because it issues fresh "
                f"ordinals rather than the journal's")

        if cursor is None and start_at > len(journal.records):
            raise RunnerError(
                f"the resume point is seq {cp.next_seq} but the journal holds "
                f"only {len(journal.records)} records — the record is longer than "
                f"this journal, so they are not the same journal. Refusing rather "
                f"than folding a short journal over a longer record")
        consumable = len(journal.records) - start_at
        if args.limit is not None:
            consumable = min(consumable, args.limit)

        if consumable == 0:
            # A no-op needs no acknowledgement: there is nothing to write, and
            # demanding a flag for a run that cannot write would make the
            # idempotency check itself awkward to execute.
            report = ApplyReport(
                org_id=args.org_id, graph_id=args.graph_id, start_at=start_at,
                journal={"path": str(journal.path),
                         "records": len(journal.records),
                         "segmented": journal.segmented,
                         "salvage": journal.salvage,
                         "corrupt_lines": journal.corrupt,
                         "torn_tails": journal.torn},
                checkpoint_before={"high_seq": cp.high_seq, "present": cp.present,
                                   "first_gap": cp.first_gap,
                                   "next_seq": cp.next_seq},
            )
            report.checkpoint_after = dict(report.checkpoint_before)
            emit(report.as_dict(), _render_apply(report), as_json=args.as_json)
            return EXIT_OK

        if args.acknowledge_plan is None:
            raise RunnerError(
                f"a run that writes requires --acknowledge-plan {consumable}: "
                f"produce the plan first (`record_runner plan`), read the number "
                f"it reports, and pass it back. {consumable} event(s) are "
                f"unconsumed from seq {start_at + 1}. This is #11469's "
                f"report-before-drop guarantee, made mechanical")
        if args.acknowledge_plan != consumable:
            raise RunnerError(
                f"--acknowledge-plan {args.acknowledge_plan} does not match this "
                f"run: {consumable} event(s) are unconsumed from seq "
                f"{start_at + 1} (the record already holds "
                f"{start_at} position(s)). The plan you read does not describe "
                f"this run: the journal grew, or the checkpoint moved. Ask for "
                f"the number this run wants — "
                f"`record_runner plan --from-checkpoint …` (a plain `plan` "
                f"reports the WHOLE journal, which is a different number and "
                f"will refuse again), or `status` for the remainder — then "
                f"acknowledge that. `--limit` gives a bounded, stable run on a "
                f"moving journal")

        report = apply_batches(
            journal=journal, org_id=args.org_id, graph_id=args.graph_id,
            conn=conn, start_at=start_at, batch_size=args.batch_size,
            limit=args.limit, allocate=args.allocate,
            timeout_ms=args.timeout_ms, echo=echo)
        report.checkpoint_before = {"high_seq": cp.high_seq, "present": cp.present,
                                    "first_gap": cp.first_gap,
                                    "next_seq": cp.next_seq}
    finally:
        _quietly(conn.close)

    # The checkpoint AFTER, read from the record rather than assumed from the
    # batch math: a report that computed it from `consumed` would agree with
    # itself by construction, which is the one thing it must not do.
    conn = open_record(args.org_id, dsn=args.dsn, timeout_ms=args.timeout_ms)
    try:
        after = read_checkpoint(conn, args.org_id, args.graph_id)
        conn.rollback()       # read-only: nothing was written, so nothing commits
    finally:
        _quietly(conn.close)
    report.checkpoint_after = {"high_seq": after.high_seq,
                               "present": after.present,
                               "first_gap": after.first_gap,
                               "next_seq": after.next_seq}
    emit(report.as_dict(), _render_apply(report), as_json=args.as_json)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    def echo(line: str) -> None:
        if not args.as_json:
            sys.stderr.write(line + "\n")

    try:
        if args.verb == "plan":
            return cmd_plan(args, echo=echo)
        if args.verb == "status":
            return cmd_status(args, echo=echo)
        return cmd_apply(args, echo=echo)
    except RunnerError as exc:
        sys.stderr.write(f"record_runner: REFUSED — {exc}\n")
        return EXIT_REFUSED
    except Exception as exc:
        # Includes `RecordProjectionError` (an undeclared record type, a cut-over
        # refusal, a statement that failed) and the driver's own errors: each is
        # a reason this run must not be claimed, so each is a refusal. The
        # message keeps its own text because that is where the module names the
        # statement and the column.
        sys.stderr.write(f"record_runner: REFUSED — {_one_line(exc)}\n")
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
