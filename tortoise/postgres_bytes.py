"""Per-org Postgres storage bytes MEASURED FROM WHAT WE WRITE (#12762).

WHAT THIS IS, IN ONE SENTENCE. The rows this product writes are keyed by
``org_id``, so the bytes THEY occupy belong to the org that wrote them — and
that is a *measurement*, not the allocation the database-grain position (#4492)
correctly refused. This module supplies the two halves:

* the **meter** — :func:`measure_written_rows` reads the stored size of the
  journal rows a caller has JUST written, in the caller's own transaction;
* the **audit** — :func:`per_org_bytes` recomputes the same figure for the
  whole table, per org, later and independently, and publishes the bytes that
  cannot be attributed per org as a **separate, labelled residual**.

The ledger write lives in :mod:`tortoise.metering` (every dimension writes
through that one contract, and a second writer would be a second convention).

⛔ THE TOAST QUESTION, SETTLED BY MEASUREMENT (2026-11-14). This issue was
filed on the premise that ``sum(pg_column_size(...))`` reports the 18-byte
TOAST pointer for a moved-out value, a ~100× undercount. **That premise is
false, and the measurement is the reason this module is shaped as it is.**
Measured on PG 17.12, PG 16.9 and PG 18.3 (the last via the repo's PGlite
harness), table shaped exactly like ``public.journal_event``:

===========  =========  ==============  ==========  ==============
payload      JSON text  pg_column_size  chunks      chunk_data
===========  =========  ==============  ==========  ==============
small        47         59              0           0
4 KB repeat  4,045      **114**         0           0
1,536-float  15,428     **7,177**       4           7,177
19 KB random 19,245     **19,256**      10          19,256
===========  =========  ==============  ==========  ==============

Three facts, each of which the design depends on:

1. ``pg_column_size`` **follows the TOAST pointer** and reports the value's
   stored size — 19,256 for the out-of-line row, i.e. its chunk sum exactly.
   No pointer can fool the figure. (The disagreeing sources resolve to "full
   stored size".)
2. It reports the **compressed** stored size where compression applies
   (15,428 → 7,177 out of line; 4,045 → 114 compressed *inline*, which is the
   only real "shock undercount" and is simply the physical truth).
3. ⇒ ``pg_column_size(t.*)`` — the whole stored row, not just the payload —
   **is** a per-row, per-org, stored-byte measurement. That is what both halves
   sum.

⛔ AND THE TRAP THAT *IS* LIVE — DOUBLE COUNTING. ``pg_column_size(t.*)``
already CONTAINS an out-of-line value's stored bytes, so adding the TOAST chunk
bytes to it double counts (measured: 7,241 + 7,177 = 14,418 for a row that
occupies 7,241). Both the meter and the audit return ``chunk_bytes`` **for
disclosure only** — it proves the value is inside the row figure and sizes the
chunk overhead the residual must carry — and it is NEVER added to a per-org
total.

⛔ THE NAIVE WRITE-TIME MEASURE IS WRONG IN THE OTHER DIRECTION.
``INSERT … RETURNING pg_column_size(payload)`` sees the datum BEFORE the row is
stored — pre-TOAST, pre-compression, and for ``jsonb`` in its uncompressed
binary form. For our own record shape (a 1,536-float vector) it reports
**24,606** bytes for a payload that occupies **7,177**: a 3.4× OVER-count,
which would over-bill. That is why the meter reads the row BACK (one indexed
read per write segment) instead of taking the free-looking RETURNING value.
``tests/test_postgres_bytes.py`` and the SQL suite carry that comparison as a
pin that goes red on the naive method.

⛔ WHAT IS NOT PER-ORG, AND WHY IT IS NOT FOLDED IN. Heap page slack, dead
tuples, the primary-key index and the TOAST chunk tuple headers are
DATABASE-grain: attributing them to an org would need an allocation rule, which
is exactly what #4492 refused. They travel in :attr:`PerOrgPostgresBytes.
residual` and never touch a per-org number.

⛔ THIS MEASURES; IT DOES NOT PRICE OR ARM. Nothing here prices, tiers, caps or
refuses. Arming ``postgres_storage`` still needs the ``free``/``anon`` included
amounts decided (a separate, owner-gated change) — the ONLY condition left
outstanding, and it is the owner's decision, not an instrument gap.

⛔ THE THIRD ARTEFACT IS ELSEWHERE, AND THIS PARAGRAPH USED TO SAY IT WAS
MISSING. The gate can also not read this module's whole-table audit on a write
path (a per-row TOAST lookup per call), and the ledger reader is the window
addend rather than the stored footprint — so for a while ``postgres_storage``
was blocked on a per-org RESIDENCY gauge as well. #9559 wires one:
``org_postgres_residency`` (migration ``20261117000001``) is refreshed OFFLINE
from :func:`per_org_bytes` by ``postgres_residency_snapshot()``, and the gate
reads one row by primary key through ``metering.get_postgres_residency``. The
divide is deliberate: this module stays the MEASUREMENT, ``metering`` owns every
dimension's READING, and ``allowance_gate`` owns the cap. The gate's own
``RELEASE_CONDITIONS`` states the state; do not restate a different one here.

FAIL-SOFT. :func:`measure_written_rows` and :func:`per_org_bytes` are
TOTAL on the live path — never raise, never change a write's outcome; a fault
is logged and reported as ``None`` / ``ok=False``. ⚠️ SCOPED TO THE METER'S OWN
FAULTS, and the scope is load-bearing rather than pedantic: the meter's
bookkeeping statements are what hold that promise up, so when they themselves
cannot be issued (``25P02`` on an already-aborted transaction, ``25P01`` outside
a block, a dead session) the caller's transaction is left ABORTED and its
``commit()`` still rolls the record write back. That arm is DISCLOSED —
:func:`_meter_statement` says so at WARNING — and NOT repaired; see
:func:`measure_written_rows` for the whole statement. The pure parsers
(:func:`parse_written_rows`, :func:`parse_per_org_bytes`) DO raise, so a
malformed payload fails its reading instead of masquerading as a zero — a
failed reading's zeros are PLACEHOLDERS, never a measured zero.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

_logger = logging.getLogger(__name__)

__all__ = [
    "AUDIT_RPC",
    "DEFAULT_EXCESS_BAND",
    "METHOD_NOTE",
    "RESIDUAL_NOTE",
    "ROWS_RPC",
    "OrgBytes",
    "PerOrgPostgresBytes",
    "Reconciliation",
    "WrittenRows",
    "measure_written_rows",
    "parse_per_org_bytes",
    "parse_written_rows",
    "per_org_bytes",
    "reconcile",
]

#: The two SQL entry points. One place, so the SQL and the caller cannot drift.
ROWS_RPC = "journal_event_bytes_for_rows"
AUDIT_RPC = "postgres_per_org_bytes"

#: The sentence a reader must see beside any per-org figure. Carried on every
#: reading so the number cannot be consumed as an invoice or a cap input
#: without it.
METHOD_NOTE = (
    "A MEASUREMENT of stored bytes, per org: sum(pg_column_size(row)) over the "
    "org's own journal rows. It is the compressed stored size where "
    "compression applies, and it follows the TOAST pointer (measured — a "
    "19,245 B out-of-line payload reports 19,256, not an 18 B pointer). It is "
    "NOT an allocation of the database total, and it is NOT invoice-grade: the "
    "vendor bill is measured by the platform."
)

#: What the figure deliberately excludes, on every reading.
RESIDUAL_NOTE = (
    "EXCLUDES (database-grain, published separately and never folded in): heap "
    "page slack and dead tuples, the primary-key index, and TOAST chunk tuple "
    "headers. Attributing those to an org would be an ALLOCATION."
)

#: How far the accumulated counter may sit ABOVE the audit before the reading
#: is called a defect. The counter is a sum of the same per-row figure the audit
#: sums, so for rows written through the seam the two agree exactly; a material
#: excess therefore means double counting (the trap this module pins), not
#: rounding. 1% is generous against integer arithmetic and far below the 2× a
#: double count produces.
DEFAULT_EXCESS_BAND = 0.01


def _as_payload(value: Any) -> Any:
    """Accept the decoded jsonb value, or a JSON STRING carrying it."""
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _as_int(value: Any, what: str) -> int:
    """Coerce a JSON number to a non-negative int. RAISES on anything else.

    ``bool`` is rejected explicitly: ``isinstance(True, int)`` is True, so a
    boolean would otherwise be counted as 1 byte.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{what} must be a number, got {value!r}")
    n = int(value)
    if n != value or n < 0:
        raise ValueError(f"{what} must be a whole, non-negative number, got {value!r}")
    return n


def _as_opt_int(value: Any, what: str) -> int | None:
    """Like :func:`_as_int`, but NULL passes through as ``None``.

    ``None`` is "the server could not report it" (PG < 17 has no
    ``pg_column_toast_chunk_id``) and must NEVER collapse to 0 — "we could not
    attribute chunks here" is not "there are no chunks".
    """
    return None if value is None else _as_int(value, what)


@dataclass(frozen=True)
class WrittenRows:
    """The stored bytes of the journal rows one write segment just wrote."""

    org_id: str
    graph_id: str
    rows: int
    row_bytes: int
    payload_bytes: int
    chunk_bytes: int | None
    chunk_detail: bool
    captured_at: str = ""

    @property
    def bytes_per_row(self) -> float:
        """Mean stored bytes per record (0.0 for an empty segment)."""
        return self.row_bytes / self.rows if self.rows else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "rows": self.rows,
            "row_bytes": self.row_bytes,
            "payload_bytes": self.payload_bytes,
            "chunk_bytes": self.chunk_bytes,
            "chunk_detail": self.chunk_detail,
            "captured_at": self.captured_at,
            "method_note": METHOD_NOTE,
        }


def parse_written_rows(payload: Any) -> WrittenRows:
    """Parse a ``journal_event_bytes_for_rows()`` payload. RAISES on malformed."""
    body = _as_payload(payload)
    if not isinstance(body, dict):
        raise ValueError(f"{ROWS_RPC} reply must be a JSON object, got {type(body).__name__}")
    org_id = body.get("org_id")
    if not isinstance(org_id, str) or not org_id:
        raise ValueError(f"{ROWS_RPC} reply has no usable org_id: {org_id!r}")
    graph_id = body.get("graph_id")
    if not isinstance(graph_id, str) or not graph_id:
        raise ValueError(f"{ROWS_RPC} reply has no usable graph_id: {graph_id!r}")
    chunk_detail = body.get("chunk_detail")
    if not isinstance(chunk_detail, bool):
        # Not cosmetic: chunk_bytes' NULL is only readable as "unavailable"
        # when this flag says so. Without it a caller cannot tell 0 from NULL.
        raise ValueError(
            f"{ROWS_RPC} reply has no boolean chunk_detail: {chunk_detail!r}")
    rows = _as_int(body.get("rows"), "rows")
    row_bytes = _as_int(body.get("row_bytes"), "row_bytes")
    payload_bytes = _as_int(body.get("payload_bytes"), "payload_bytes")
    chunk_bytes = _as_opt_int(body.get("chunk_bytes"), "chunk_bytes")
    # ⛔ CONTAINMENT — the invariant that catches the NAIVE reading.
    # A stored row CONTAINS its payload, so payload_bytes can never exceed
    # row_bytes. The value `INSERT … RETURNING pg_column_size(payload)` returns
    # is the datum BEFORE the row is stored (pre-TOAST, pre-compression, jsonb
    # in its uncompressed binary form) and for our own record shape it is 3.4x
    # the stored size — so a payload built from it VIOLATES this inequality.
    # A meter that accepted it would over-bill; refusing it here is what makes
    # the falsifier a guard rather than a comment.
    if payload_bytes > row_bytes:
        raise ValueError(
            f"{ROWS_RPC} reply is inconsistent: payload_bytes={payload_bytes} "
            f"exceeds row_bytes={row_bytes}. A stored row contains its payload, "
            f"so this is the PRE-TOAST/RETURNING size (measured 3.4x the stored "
            f"size for a vector payload), not a stored reading.")
    if chunk_bytes is not None and chunk_bytes > row_bytes:
        # Same invariant one level down: the out-of-line value is INSIDE the
        # row figure, so chunk bytes above it is the row+chunks DOUBLE COUNT.
        raise ValueError(
            f"{ROWS_RPC} reply is inconsistent: chunk_bytes={chunk_bytes} "
            f"exceeds row_bytes={row_bytes} — row_bytes already contains the "
            f"out-of-line value, so this is the double-count trap.")
    return WrittenRows(
        org_id=org_id,
        graph_id=graph_id,
        rows=rows,
        row_bytes=row_bytes,
        payload_bytes=payload_bytes,
        chunk_bytes=chunk_bytes,
        chunk_detail=chunk_detail,
        captured_at=str(body.get("captured_at") or ""),
    )


#: The savepoint the meter runs under inside a caller's transaction.
#: One name: the meter is called once per write segment and never nested.
_METER_SAVEPOINT = "tortoise_byte_meter"


def _in_caller_transaction(conn: Any) -> bool:
    """Does a statement here JOIN a transaction the CALLER will commit?

    ``False`` under ``autocommit`` (each statement is its own transaction, so a
    failing one aborts nothing the caller owns). ``True`` otherwise — and there
    the savepoint is load-bearing, because psycopg2/psycopg both leave the
    transaction ABORTED after a failed statement and the caller's ``commit()``
    then silently becomes a ROLLBACK.

    Read from the driver's own flag rather than from a cursor, so it holds for
    both psycopg2 and psycopg3 and for any duck-typed test double that models
    ``autocommit`` (a double without the attribute is treated as
    transactional — the safe default, since a savepoint on a connection that
    cannot hold one is itself caught by the caller's ``except``).
    """
    return not bool(getattr(conn, "autocommit", False))


def _read_written_rows(
    cur: Any, *, org_id: str, graph_id: str, positions: list[int]
) -> WrittenRows:
    cur.execute(
        f"SELECT public.{ROWS_RPC}(%s, %s, %s)",
        (org_id, graph_id, positions),
    )
    row = cur.fetchone()
    if not row:
        raise ValueError(f"{ROWS_RPC} returned no row")
    return parse_written_rows(row[0])


def _meter_statement(cur: Any, statement: str, *, why: str) -> None:
    """Issue one savepoint statement, or SAY the transaction may now be dirty.

    ⛔ THE RECOVERY IS THE SAFETY PROPERTY, SO ITS OWN FAILURE MUST NOT LOOK LIKE
    THE HARMLESS ONE. ``measure_written_rows`` is total — every failure is logged
    at DEBUG and returns ``None`` — and that is right for *reading* the meter.
    It is wrong for the savepoint bookkeeping: if ``ROLLBACK TO SAVEPOINT`` or a
    ``RELEASE`` cannot be issued, the caller's transaction is left **ABORTED**,
    which is the exact #12781 state — its ``commit()`` then rolls the record
    write back. Logging that at DEBUG as ``measurement failed (non-fatal)``,
    beside the benign case it must never be confused with, is how a data-loss
    path hides inside a warning-free log.

    So this raises *after* naming the consequence at WARNING, distinct from the
    outer handler's DEBUG skip. Nothing here can repair it from the inside —
    only the caller knows whether its transaction is still worth committing —
    and the outer ``except`` still returns ``None`` so the meter stays total.

    ⛔ THAT MAKES THIS ARM DISCLOSED, NOT REPAIRED, and it is stated here so the
    code is not read as more than it is: a caller that commits into a
    transaction this arm could not recover still rolls its rows back. The
    realistic triggers (``25P02`` on an already-aborted transaction, ``25P01``
    outside a block, a dead connection) all mean that write was already doomed
    before the meter ran, so no new loss is introduced — but the WARNING is the
    whole of the protection in this branch, which is why it may never be
    downgraded to the DEBUG skip.
    """
    try:
        cur.execute(statement)
    except Exception:
        _logger.warning(
            "the byte meter could not issue `%s` (%s) — the caller's transaction "
            "may now be ABORTED, so its commit() would roll the record write "
            "back (#12781). This is NOT the harmless 'measurement skipped' "
            "path: the meter is best-effort, the record is not.",
            statement, why, exc_info=True,
        )
        raise


def measure_written_rows(
    conn: Any,
    *,
    org_id: str,
    graph_id: str,
    seqs: Sequence[int] | Iterable[int],
) -> WrittenRows | None:
    """Read the stored bytes of the rows just written. TOTAL — never raises.

    Call this in the SAME transaction as the insert, so the rows it sums are
    the ones this write put there — and so what it reports is what Postgres
    STORED (post-TOAST, post-compression), which is exactly what the audit will
    see later. Returns ``None`` on any failure (no connection, an unarmed
    record, a missing function on an older schema, a malformed reply), logging
    at DEBUG: a metering fault must never change a write's outcome.

    ⛔ "NEVER CHANGE A WRITE'S OUTCOME" IS ENFORCED FOR THE METER'S OWN
    FAILURES — AND ONLY FOR THOSE (#12762 follow-up, `main` red at
    `9e934efad5`). Swallowing the exception was NOT
    enough: the meter runs inside the caller's transaction, and in PostgreSQL a
    failed statement leaves that transaction ABORTED — so the caller's
    succeeding `commit()` becomes a ROLLBACK and the whole record write is
    destroyed, silently, while the writer reports ``APPLIED``. MEASURED on PG16
    with the meter refused (``InsufficientPrivilege``): without a savepoint the
    inserted row is gone after `commit()`; with `SAVEPOINT` +
    `ROLLBACK TO SAVEPOINT` it is present. That is the difference between a
    metering fault and data loss, and every trigger of it is mundane — the
    metering migration not yet applied on a fixture database
    (``UndefinedFunction``), the app role missing ``EXECUTE``
    (``InsufficientPrivilege``, which #12764 shipped), a lock timeout.

    The savepoint does NOT skew the measurement, which is why it is a savepoint
    and not a second connection: the function attributes rows by
    ``xmin = pg_current_xact_id()``, and for a subtransaction that performs NO
    WRITE — the meter is a read — PostgreSQL keeps the TOP-LEVEL xid. MEASURED
    on PG16: inside the savepoint `pg_current_xact_id()` returned the same
    ``919`` as before it, and the function still reported ``rows: 1`` for the
    row inserted before the savepoint.

    ⛔ THE RESIDUAL ARM, NAMED SO THE CLAIM IS NOT WIDER THAN THE CODE. The
    savepoint makes the meter's *own* failure — the read, the refusal, the lock
    timeout — unable to abort the caller's transaction. It does NOT rescue a
    transaction that is ALREADY unusable: if the ``SAVEPOINT``/``RELEASE``
    bookkeeping itself cannot be issued (``25P02``
    in-failed-sql-transaction, ``25P01`` outside a block, a dead connection),
    ``_meter_statement`` discloses it at WARNING and this function is still
    total, so a caller that commits anyway still rolls its rows back. That arm
    is DISCLOSED, not repaired — nothing here can repair it. It is also not a
    new loss path: those triggers mean the write was already doomed by whatever
    aborted or dropped the session, which is precisely why the disclosure is a
    WARNING and not the DEBUG skip the benign case gets.

    ⛔ SUPERSEDED CONTRACT TEXT, NAMED SO IT IS NOT RE-DERIVED WRONGLY. The
    header of `supabase/migrations/20261114000001_per_org_postgres_bytes.sql`
    still says *"Under a SAVEPOINT the row's xmin is the sub-transaction's, so
    the filter UNDER-counts … and the record write path takes no savepoints
    today"*. The **second** half is now false: this door takes a savepoint on
    every armed, positioned write. The **first** half is still TRUE as a
    statement about PostgreSQL — it describes a savepoint that CONTAINS the
    insert — and that is precisely why the savepoint here sits AFTER the insert,
    so the subtransaction writes nothing and the top-level xid is preserved
    (measured above). ⛔ Both orderings are pinned, so neither half is a claim
    you have to take on trust — by TWO artifacts, because one cannot answer the
    other's question:
    `supabase/tests/20261114000001_per_org_postgres_bytes.sql` §7b pins the
    SEMANTICS (`rows: 1` for savepoint-after-insert, `rows: 0` for
    savepoint-before-insert) but is a static script and never calls this module;
    `tests/test_postgres_bytes.py::test_the_meter_call_lands_AFTER_the_insert_it_measures`
    pins the SHIPPED CALL ORDER on the source, which is what reds if the meter
    is moved before the insert. The
    migration files are APPEND-ONLY (`check-migration-append-only` refuses a
    modified `supabase/migrations/*.sql`), so that sentence cannot be corrected
    where it lives; this paragraph and
    `docs/ops/postgres-storage-measurement.md` are the correction. ⛔ Do NOT
    read the header as a licence to delete the savepoint: that re-creates
    #12781 — every record write silently rolled back by a metering fault.

    ``seqs`` is a sequence of journal positions — one indexed read per segment,
    not one per record.
    """
    try:
        positions = [int(s) for s in seqs]
        if not positions:
            return None
        if conn is None:
            raise ValueError("no record connection supplied")
        with conn.cursor() as cur:
            if not _in_caller_transaction(conn):
                # autocommit: a failure aborts nothing the caller owns.
                return _read_written_rows(
                    cur, org_id=org_id, graph_id=graph_id, positions=positions)
            _meter_statement(
                cur, f"SAVEPOINT {_METER_SAVEPOINT}", why="opening the meter's savepoint")
            try:
                measured = _read_written_rows(
                    cur, org_id=org_id, graph_id=graph_id, positions=positions)
            except Exception:
                # Undo ONLY the failed read, so the caller's transaction is
                # usable and its rows — inserted BEFORE this savepoint — commit.
                _meter_statement(
                    cur, f"ROLLBACK TO SAVEPOINT {_METER_SAVEPOINT}",
                    why="recovering a failed meter read")
                _meter_statement(
                    cur, f"RELEASE SAVEPOINT {_METER_SAVEPOINT}",
                    why="after the rollback")
                raise
            _meter_statement(
                cur, f"RELEASE SAVEPOINT {_METER_SAVEPOINT}",
                why="after a successful meter read")
        return measured
    except Exception:  # a meter must never break the write
        _logger.debug("postgres byte measurement failed (non-fatal)", exc_info=True)
        return None


@dataclass(frozen=True)
class OrgBytes:
    """One org's attributable stored bytes, as the audit sees them."""

    org_id: str
    rows: int
    row_bytes: int
    payload_bytes: int
    chunk_bytes: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "rows": self.rows,
            "row_bytes": self.row_bytes,
            "payload_bytes": self.payload_bytes,
            "chunk_bytes": self.chunk_bytes,
        }


@dataclass(frozen=True)
class PerOrgPostgresBytes:
    """The audit reading: per-org stored bytes + the residual, kept apart."""

    orgs: tuple[OrgBytes, ...]
    rows: int
    attributed_bytes: int
    residual: dict[str, Any] = field(default_factory=dict)
    chunk_detail: str = ""
    captured_at: str = ""
    ok: bool = True
    error: str | None = None
    method_note: str = METHOD_NOTE
    residual_note: str = RESIDUAL_NOTE

    def for_org(self, org_id: str) -> OrgBytes | None:
        """The org's row, or ``None`` when the audit does not cover it.

        ``None`` is "not covered" — deliberately NOT a zero-byte org.
        """
        for o in self.orgs:
            if o.org_id == org_id:
                return o
        return None

    @property
    def residual_bytes(self) -> int | None:
        """The un-attributable remainder, or ``None`` when it was not reported."""
        value = self.residual.get("residual_bytes")
        return None if value is None else int(value)

    @property
    def residual_ratio(self) -> float | None:
        value = self.residual.get("residual_ratio")
        return None if value is None else float(value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "postgres_per_org_bytes": [o.as_dict() for o in self.orgs],
            "postgres_per_org_rows": self.rows,
            "postgres_attributed_bytes": self.attributed_bytes,
            "postgres_residual": dict(self.residual),
            "postgres_chunk_detail": self.chunk_detail,
            "postgres_captured_at": self.captured_at,
            "postgres_ok": self.ok,
            "postgres_error": self.error,
            "postgres_method_note": self.method_note,
            "postgres_residual_note": self.residual_note,
        }


def parse_per_org_bytes(payload: Any) -> PerOrgPostgresBytes:
    """Parse a ``postgres_per_org_bytes()`` payload. RAISES on malformed."""
    body = _as_payload(payload)
    if not isinstance(body, dict):
        raise ValueError(f"{AUDIT_RPC} reply must be a JSON object, got {type(body).__name__}")
    raw_orgs = body.get("orgs")
    if not isinstance(raw_orgs, list):
        raise ValueError(f"{AUDIT_RPC} reply has no orgs array")
    orgs: list[OrgBytes] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_orgs):
        if not isinstance(raw, dict):
            raise ValueError(f"orgs[{i}] must be an object, got {type(raw).__name__}")
        org_id = raw.get("org_id")
        if not isinstance(org_id, str) or not org_id:
            raise ValueError(f"orgs[{i}] has no usable org_id: {org_id!r}")
        if org_id in seen:
            # A duplicate would double one org's bytes in any sum a caller
            # makes. Fail rather than publish an ambiguous list.
            raise ValueError(f"orgs[{i}] repeats org {org_id!r}")
        seen.add(org_id)
        chunk_bytes = _as_opt_int(raw.get("chunk_bytes"), f"{org_id}.chunk_bytes")
        row_bytes = _as_int(raw.get("row_bytes"), f"{org_id}.row_bytes")
        if chunk_bytes is not None and chunk_bytes > row_bytes:
            raise ValueError(
                f"orgs[{i}] ({org_id}) reports chunk_bytes={chunk_bytes} above "
                f"row_bytes={row_bytes}: row_bytes already contains the "
                f"out-of-line value, so that is the row+chunks double count")
        orgs.append(
            OrgBytes(
                org_id=org_id,
                rows=_as_int(raw.get("rows"), f"{org_id}.rows"),
                row_bytes=row_bytes,
                payload_bytes=_as_int(raw.get("payload_bytes"), f"{org_id}.payload_bytes"),
                chunk_bytes=chunk_bytes,
            )
        )
    rows = _as_int(body.get("rows"), "rows")
    attributed = _as_int(body.get("attributed_bytes"), "attributed_bytes")
    if rows != sum(o.rows for o in orgs):
        raise ValueError(
            f"{AUDIT_RPC} reply is inconsistent: rows={rows} but the orgs sum "
            f"to {sum(o.rows for o in orgs)}")
    if attributed != sum(o.row_bytes for o in orgs):
        raise ValueError(
            f"{AUDIT_RPC} reply is inconsistent: attributed_bytes={attributed} "
            f"but the orgs sum to {sum(o.row_bytes for o in orgs)}")
    raw_residual = body.get("residual")
    if not isinstance(raw_residual, dict):
        raise ValueError(f"{AUDIT_RPC} reply has no residual object — the "
                         f"un-attributable bytes must be reported, not omitted")
    return PerOrgPostgresBytes(
        orgs=tuple(orgs),
        rows=rows,
        attributed_bytes=attributed,
        residual=raw_residual,
        chunk_detail=str(body.get("chunk_detail") or ""),
        captured_at=str(body.get("captured_at") or ""),
    )


def per_org_bytes(cp: Any) -> PerOrgPostgresBytes:
    """Read the per-org audit through the control-plane seam. TOTAL.

    *cp* is a :class:`~tortoise.supabase_control.SupabaseControlPlane` (or any
    object exposing ``rpc_value``). Returns ``ok=False`` on any failure and
    never raises.
    """
    try:
        if cp is None:
            raise ValueError("no control-plane handle supplied")
        return parse_per_org_bytes(cp.rpc_value(AUDIT_RPC, {}))
    except Exception as e:  # a measurement fault is a reading
        _logger.debug("per-org postgres bytes audit failed (non-fatal)", exc_info=True)
        return PerOrgPostgresBytes(
            orgs=(), rows=0, attributed_bytes=0,
            ok=False, error=str(e)[:300],
        )


@dataclass(frozen=True)
class Reconciliation:
    """The counter checked against the audit — with the direction named.

    ``unmetered_bytes`` (audit ABOVE the counter) is EXPECTED and is not an
    error: it is the org's history from before the meter existed, plus any row
    written through a door that skipped the seam. ``excess_bytes`` (counter
    ABOVE the audit) is the dangerous direction — it means the counter
    double-counted or counted something that is not stored — and only it can
    make the reading not-ok, because over-counting is what would over-bill.
    """

    org_id: str
    metered_bytes: int
    audited_bytes: int
    excess_bytes: int
    unmetered_bytes: int
    band: float
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id,
            "metered_bytes": self.metered_bytes,
            "audited_bytes": self.audited_bytes,
            "excess_bytes": self.excess_bytes,
            "unmetered_bytes": self.unmetered_bytes,
            "band": self.band,
            "ok": self.ok,
            "detail": self.detail,
        }


def reconcile(
    org_id: str,
    *,
    metered_bytes: int,
    audited_bytes: int,
    band: float = DEFAULT_EXCESS_BAND,
) -> Reconciliation:
    """Compare the accumulated counter with the audit for one org.

    The counter may sit BELOW the audit (pre-meter history, or a write that
    bypassed the seam) — that is disclosed, never silently absorbed. It may not
    sit materially ABOVE it: the two sum the same per-row figure, so an excess
    is the double-count trap or a fabricated number, and the reading says which
    way it failed instead of quietly under-billing.
    """
    if metered_bytes < 0 or audited_bytes < 0:
        raise ValueError("byte counts must be non-negative")
    excess = max(0, metered_bytes - audited_bytes)
    unmetered = max(0, audited_bytes - metered_bytes)
    base = audited_bytes if audited_bytes else 1
    ratio = excess / base
    ok = ratio <= band
    if ok:
        detail = (
            f"{org_id}: counter {metered_bytes} B within the audit's "
            f"{audited_bytes} B (excess {excess} B = {ratio:.4%}, band "
            f"{band:.2%}; unmetered {unmetered} B disclosed)")
    else:
        detail = (
            f"{org_id}: the counter reports {metered_bytes} B against an "
            f"audit of {audited_bytes} B — {excess} B ({ratio:.2%}) ABOVE the "
            f"band {band:.2%}. The two sum the SAME per-row figure, so an "
            f"excess is a double count (row + TOAST chunks) or a fabricated "
            f"figure; investigate before this number is used anywhere.")
    return Reconciliation(
        org_id=org_id,
        metered_bytes=metered_bytes,
        audited_bytes=audited_bytes,
        excess_bytes=excess,
        unmetered_bytes=unmetered,
        band=band,
        ok=ok,
        detail=detail,
    )
