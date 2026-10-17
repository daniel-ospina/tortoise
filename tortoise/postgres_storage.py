"""Supabase/Postgres STORAGE FOOTPRINT + per-org ANALYTICS ROW GROWTH (#4492).

WHY THIS EXISTS. The B7 cost-coverage audit found two dimensions with no
primitive at all:

* **The control plane's on-disk footprint was unmeasured.** ``git grep`` for
  ``pg_database_size`` / ``pg_total_relation_size`` / ``row_count`` over
  ``tortoise/`` returned nothing, so the Supabase tier we must buy could only
  be sized on guesswork.
* **``analytics_events`` volume was counted by OUTCOME, not by org.**
  ``monitoring.ANALYTICS_OUTCOME_COUNT`` labels by terminal outcome only, so
  the org that produced the rows could not be identified — an org generating
  disproportionate telemetry volume was indistinguishable from a quiet one.

WHAT THIS MEASURES. Two read-only Postgres functions (migration
``20261015000001_postgres_storage_footprint.sql``), reached through the
control-plane RPC seam:

1. :func:`measure_postgres_storage` → ``postgres_storage_footprint()``: the
   database's total on-disk bytes plus a per-table breakdown (total / heap /
   index bytes and the planner's live-row estimate).
2. :func:`read_analytics_row_growth` → ``analytics_events_rows_by_org(since,
   until)``: an EXACT per-org ``count(*)`` for ``analytics_events`` in a
   half-open window — an org's analytics row growth.

⛔ **THE ATTRIBUTION BOUNDARY — bytes are DATABASE-grain, rows are per-org.**
Postgres records no per-org byte ownership, so a per-org BYTE figure would be
an ALLOCATION mislabelled as a measurement. This module therefore publishes
exactly two things, each named for what it is: a DATABASE/TABLE-grain byte
footprint, and a PER-ORG ROW count. The parent substrate (#5045) is where an
allocation rule would be declared, if one is ever wanted; nothing here
invents one.

⛔ **PRECISION.** ``total_bytes``/``heap_bytes``/``index_bytes`` are EXACT
on-disk figures. ``live_rows`` is ``pg_stat_user_tables.n_live_tup`` — a
PLANNER STATISTIC refreshed by autovacuum/ANALYZE, so it can lag the truth;
the exact count is the row-growth function's ``count(*)``. Neither figure is
invoice-grade: the vendor bill is measured by the platform, not here.

⛔ **THIS MEASURES; IT DOES NOT PRICE.** No price, tier boundary, quota,
entitlement, cap or retention decision is made here. ``product/pricing.json``
is untouched, nothing is refused or throttled, and no Prometheus counter is
registered: a counter labelled by org for a figure that has no per-org
attribution would be a fabricated measurement (see the attribution boundary
above), and the #4493 lesson is that a registered-but-unwritten metric reads
as a measurement while being a lie. The read path IS the SQL function (a
person runs ``select postgres_storage_footprint();``); this module is the
consumable, testable, honesty-carrying wrapper for code.

**RETENTION — READ THIS BEFORE ADDING A PRUNE.** :data:`RETENTION_FINDING` is
carried on every growth reading. ``analytics_events`` has **no retention
path**: migration 0004 installs a statement-level ``BEFORE UPDATE OR DELETE``
trigger that raises, with no role gate, and the owner's standing ruling is
*"no retention window of any length"* — the rows survive an org erasure
decoupled from the deleted identity. A sweep that expects a bounded table
will be wrong, and this module does not quietly change that.

FAIL-SOFT. :func:`measure_postgres_storage` and
:func:`read_analytics_row_growth` are TOTAL — never raise. A measurement fault
must never fail a request; a failure is returned as a reading with ``ok=False``
and an ``error`` string (logged at DEBUG), never propagated. The pure parsers
(:func:`parse_storage_snapshot`, :func:`parse_row_growth`) DO raise, so a
malformed payload fails its reading rather than masquerading as a zero.

⛔ **A FAILED READING'S ZEROS ARE PLACEHOLDERS, NOT A MEASUREMENT.**
:func:`_failed_storage_reading` carries ``database_bytes=0`` and
:func:`_failed_growth` carries ``total_rows=0``; those zeros exist because the
fields are non-optional, NOT because zero was observed. ``ok=False`` (and
``error``) is the ONLY thing that distinguishes "unmeasured" from a measured
zero — a caller that reads a count or
:meth:`AnalyticsRowGrowth.for_org` without first checking ``ok`` sees "0 rows",
not "unmeasured".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger(__name__)

__all__ = [
    "ATTRIBUTION_NOTE",
    "FOOTPRINT_RPC",
    "FOOTPRINT_TABLES",
    "PRECISION_NOTE",
    "RETENTION_FINDING",
    "ROW_GROWTH_RPC",
    "AnalyticsRowGrowth",
    "PostgresStorageReading",
    "TableFootprint",
    "measure_postgres_storage",
    "parse_row_growth",
    "parse_storage_snapshot",
    "read_analytics_row_growth",
]

#: The two read RPC names. One place, so the SQL and the caller cannot drift.
FOOTPRINT_RPC = "postgres_storage_footprint"
ROW_GROWTH_RPC = "analytics_events_rows_by_org"

#: The useful SUBSET named for a human reader (issue #4492: "total size and
#: per-table size (or the useful subset: ``analytics_events``, ``orgs``,
#: metering/usage tables)"). The RPC returns EVERY public table; this is the
#: convenience list for a caller that wants the cost-relevant three without
#: hard-coding names. A name absent from a reading is simply skipped by
#: :meth:`PostgresStorageReading.bytes_for` — never treated as zero.
FOOTPRINT_TABLES: tuple[str, ...] = (
    "analytics_events",
    "metering_records",
    "organizations",
)

#: The one sentence a reader must see. Carried on every reading (and in
#: ``as_dict()``) so the figure cannot be consumed as an invoice or a cap
#: input without it.
PRECISION_NOTE = (
    "On-disk bytes are EXACT; `live_rows` is a PLANNER STATISTIC "
    "(pg_stat_user_tables.n_live_tup, refreshed by autovacuum/ANALYZE), not a "
    "count — the exact count is analytics_events_rows_by_org's count(*). "
    "NOT invoice-grade: the vendor bill is measured by the platform."
)

#: The attribution boundary, on every reading: what can and cannot be said
#: per org. This is the property that keeps the number from being read as a
#: per-org cost.
ATTRIBUTION_NOTE = (
    "DISTRIBUTION: the byte footprint is DATABASE/TABLE-grain — Postgres "
    "records no per-org byte ownership, so a per-org byte split would be an "
    "ALLOCATION, not a measurement, and is not offered here. Per-org "
    "attribution exists for ROW GROWTH "
    "(read_analytics_row_growth → analytics_events_rows_by_org)."
)

#: The retention finding (issue #4492 indicator 3), carried on every growth
#: reading so a consumer of the number also receives the structural fact that
#: bounds it.
RETENTION_FINDING = (
    "analytics_events has NO retention path. Migration 0004 installs a "
    "statement-level BEFORE UPDATE OR DELETE trigger "
    "(`analytics_events_immutable`) that RAISE EXCEPTIONs with no role gate, "
    "so nothing can prune the table at any age; the owner's standing ruling is "
    "'no retention window of any length' and the product-usage rows survive an "
    "org erasure decoupled from the deleted identity (docs/retention-and-"
    "deletion.md; owner decisions #3843/#3853). Growth is therefore monotonic "
    "by design, and any prune is a product/legal decision, not this module's."
)


def _as_int(value: Any, what: str) -> int:
    """Coerce a JSON number to a non-negative int. RAISES on anything else.

    ``bool`` is rejected EXPLICITLY: ``isinstance(True, int)`` is True, so a
    boolean would otherwise be counted as 1 — a silent numeric lie from a
    payload that meant "yes". Accepts whole floats (JSON has no int/float
    distinction for a tool that re-serialises), rejects fractions and
    non-finite values, which are not byte counts.
    """
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{what} must be a number, got {value!r}")
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError) as e:
        raise ValueError(f"{what} must be a number, got {value!r}") from e
    if n != value or n < 0:
        raise ValueError(f"{what} must be a whole, non-negative number, got {value!r}")
    return n


def _as_payload(value: Any) -> Any:
    """Accept the decoded jsonb value, or a JSON STRING carrying it.

    PostgREST returns a ``jsonb`` function result as the bare decoded JSON
    body, which is what ``SupabaseControlPlane.rpc_value`` hands back. A
    transport that left the body as text (a proxy, a differently-configured
    driver) would hand back a string — decoding it here is the difference
    between a measurement and a spurious ``ok=False``.
    """
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        return json.loads(value)
    return value


def _failed_storage_reading(measured_at: str, error: Any) -> PostgresStorageReading:
    """A total, zero-valued reading carrying the failure — never raises."""
    return PostgresStorageReading(
        database_bytes=0,
        tables=(),
        captured_at="",
        ok=False,
        error=str(error)[:300],
        measured_at=measured_at,
    )


@dataclass(frozen=True)
class TableFootprint:
    """One table's on-disk footprint and planner row estimate."""

    name: str
    total_bytes: int
    heap_bytes: int
    index_bytes: int
    live_rows: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "total_bytes": self.total_bytes,
            "heap_bytes": self.heap_bytes,
            "index_bytes": self.index_bytes,
            "live_rows": self.live_rows,
        }


@dataclass(frozen=True)
class PostgresStorageReading:
    """One database-grain footprint reading, with its caveats attached.

    ``database_bytes`` is the whole database (system catalogs, WAL-adjacent
    relations and every schema included), NOT the sum of ``tables`` — the
    residual is what lives outside ``public``. ``tables`` is ordered by
    ``total_bytes`` descending, as the SQL returns it.

    ``live_rows_are_estimates`` is always True and ``excludes`` /
    ``precision_note`` / ``attribution_note`` travel with the number so they
    cannot be separated from it.
    """

    database_bytes: int
    tables: tuple[TableFootprint, ...]
    captured_at: str
    ok: bool = True
    error: str | None = None
    measured_at: str = ""
    live_rows_are_estimates: bool = True
    precision_note: str = PRECISION_NOTE
    attribution_note: str = ATTRIBUTION_NOTE

    def table(self, name: str) -> TableFootprint | None:
        """The footprint for *name*, or ``None`` when the table is absent.

        ``None`` is "the reading does not cover it" — deliberately NOT a
        zero-byte table, which would claim a measurement we do not have.
        """
        for t in self.tables:
            if t.name == name:
                return t
        return None

    def bytes_for(self, *names: str) -> int:
        """Sum ``total_bytes`` over the present members of *names*.

        The useful-subset read: ``reading.bytes_for(*FOOTPRINT_TABLES)``. A
        name the reading does not cover contributes nothing, and the caller
        can tell coverage from ``reading.table(name) is None``.
        """
        want = set(names)
        return sum(t.total_bytes for t in self.tables if t.name in want)

    @property
    def tables_bytes(self) -> int:
        """The summed footprint of the tables the reading covers."""
        return sum(t.total_bytes for t in self.tables)

    @property
    def residual_bytes(self) -> int:
        """``database_bytes`` minus the covered tables — everything else.

        System catalogs, other schemas and relations the reading does not
        enumerate. Never negative (clamped at 0): a table sum above the
        database total would mean the two came from different instants.
        """
        return max(0, self.database_bytes - self.tables_bytes)

    def as_dict(self) -> dict[str, Any]:
        """Serialize the reading — caveats INCLUDED, so they travel with it."""
        return {
            "postgres_database_bytes": self.database_bytes,
            "postgres_tables_bytes": self.tables_bytes,
            "postgres_residual_bytes": self.residual_bytes,
            "postgres_tables": [t.as_dict() for t in self.tables],
            "postgres_live_rows_are_estimates": self.live_rows_are_estimates,
            "postgres_precision_note": self.precision_note,
            "postgres_attribution_note": self.attribution_note,
            "postgres_captured_at": self.captured_at,
            "postgres_ok": self.ok,
            "postgres_error": self.error,
        }


def parse_storage_snapshot(payload: Any, *, measured_at: str = "") -> PostgresStorageReading:
    """Parse a ``postgres_storage_footprint()`` payload. RAISES on malformed.

    A malformed payload (a missing ``database_bytes``, a non-numeric byte
    field, a boolean carrying a number) must FAIL the reading rather than
    silently masquerade as an empty or zero-footprint database — the honesty
    guard the fail-soft caller depends on.
    """
    body = _as_payload(payload)
    if not isinstance(body, dict):
        raise ValueError(f"{FOOTPRINT_RPC} reply must be a JSON object, got {type(body).__name__}")
    if "database_bytes" not in body:
        raise ValueError(f"{FOOTPRINT_RPC} reply has no database_bytes field")
    database_bytes = _as_int(body["database_bytes"], "database_bytes")
    raw_tables = body.get("tables")
    if not isinstance(raw_tables, list):
        raise ValueError(f"{FOOTPRINT_RPC} reply has no tables array")
    tables: list[TableFootprint] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_tables):
        if not isinstance(raw, dict):
            raise ValueError(f"tables[{i}] must be an object, got {type(raw).__name__}")
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"tables[{i}] has no usable name: {name!r}")
        if name in seen:
            # The RPC groups by relname within schema 'public'; a duplicate
            # would make the breakdown ambiguous. Fail rather than double-count.
            raise ValueError(f"tables[{i}] repeats table {name!r}")
        seen.add(name)
        live = raw.get("live_rows")
        tables.append(
            TableFootprint(
                name=name,
                total_bytes=_as_int(raw.get("total_bytes"), f"{name}.total_bytes"),
                heap_bytes=_as_int(raw.get("heap_bytes"), f"{name}.heap_bytes"),
                index_bytes=_as_int(raw.get("index_bytes"), f"{name}.index_bytes"),
                # NULL is "the planner did not report it" (the SQL coalesces to 0,
                # but a different deploy may not) — NEVER folded to 0, which would
                # claim an exactly-empty table.
                live_rows=None if live is None else _as_int(live, f"{name}.live_rows"),
            )
        )
    captured_at = body.get("captured_at")
    return PostgresStorageReading(
        database_bytes=database_bytes,
        tables=tuple(tables),
        captured_at="" if captured_at is None else str(captured_at),
        ok=True,
        error=None,
        measured_at=measured_at or datetime.now(UTC).isoformat(),
    )


def measure_postgres_storage(cp: Any) -> PostgresStorageReading:
    """Read the control plane's storage footprint through *cp*. TOTAL.

    *cp* is a :class:`~tortoise.supabase_control.SupabaseControlPlane` (or any
    object exposing ``rpc_value``). Returns ``ok=False`` on any failure — a
    missing handle, a dead control plane, a malformed reply — and never raises.
    """
    measured_at = datetime.now(UTC).isoformat()
    try:
        if cp is None:
            raise ValueError("no control-plane handle supplied")
        payload = cp.rpc_value(FOOTPRINT_RPC, {})
        return parse_storage_snapshot(payload, measured_at=measured_at)
    except Exception as e:
        _logger.debug("postgres storage measurement failed (non-fatal)", exc_info=True)
        return _failed_storage_reading(measured_at, e)


def _failed_growth(
    since: str, until: str | None, measured_at: str, error: Any
) -> AnalyticsRowGrowth:
    """A total, zero-valued growth reading carrying the failure — never raises.

    The zeros here (``total_rows=0``, empty ``rows_by_org``) are PLACEHOLDERS
    that exist because the fields are non-optional — they are not a measured
    zero. They travel with ``ok=False`` and ``error``, which the caller must
    branch on before reading any count.
    """
    return AnalyticsRowGrowth(
        since=since,
        until=until,
        total_rows=0,
        rows_by_org={},
        ok=False,
        error=str(error)[:300],
        measured_at=measured_at,
    )


@dataclass(frozen=True)
class AnalyticsRowGrowth:
    """Per-org ``analytics_events`` rows created in a half-open window.

    ``rows_by_org`` maps org id → exact ``count(*)``; ``total_rows`` is the
    window total and is required to equal the sum of the map (the parser fails
    a payload where it does not, so a truncated or hand-edited reply cannot
    publish an internally inconsistent figure). ``until`` is ``None`` for an
    open-ended window.

    The retention finding travels on the reading (:data:`RETENTION_FINDING`)
    because the growth is monotonic by design — a reader that sees the number
    must also see that no prune bounds it.
    """

    since: str
    until: str | None
    total_rows: int
    rows_by_org: dict[str, int] = field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    measured_at: str = ""
    retention_finding: str = RETENTION_FINDING

    def for_org(self, org_id: str) -> int:
        """The org's exact row count in the window (0 when it had none).

        On a FAILED reading (``ok=False``) this returns 0 for every org — that
        0 is a placeholder for "unmeasured", not a measured "no rows". Check
        ``ok`` (or ``error``) before treating the value as a measurement.
        """
        return self.rows_by_org.get(org_id, 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analytics_since": self.since,
            "analytics_until": self.until,
            "analytics_total_rows": self.total_rows,
            "analytics_rows_by_org": dict(self.rows_by_org),
            "analytics_retention_finding": self.retention_finding,
            "analytics_ok": self.ok,
            "analytics_error": self.error,
        }


def _iso(value: Any, what: str) -> str | None:
    """Normalise a window bound to an ISO-8601 string; ``None`` passes through.

    A ``datetime`` is accepted for convenience; a NAIVE one RAISES, because a
    window bound with no offset would be interpreted by the server in its own
    timezone and move the window silently.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                f"{what} must be timezone-aware (a naive datetime would be "
                "interpreted in the server's timezone)"
            )
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError(f"{what} must be a datetime or an ISO-8601 string, got {value!r}")


def parse_row_growth(payload: Any, *, measured_at: str = "") -> AnalyticsRowGrowth:
    """Parse an ``analytics_events_rows_by_org()`` payload. RAISES on malformed."""
    body = _as_payload(payload)
    if not isinstance(body, dict):
        raise ValueError(f"{ROW_GROWTH_RPC} reply must be a JSON object, got {type(body).__name__}")
    since = body.get("since")
    until = body.get("until")
    if not isinstance(since, str) or not since:
        raise ValueError(f"{ROW_GROWTH_RPC} reply has no since field")
    rows_raw = body.get("rows_by_org")
    if not isinstance(rows_raw, dict):
        raise ValueError(f"{ROW_GROWTH_RPC} reply has no rows_by_org object")
    rows: dict[str, int] = {}
    for org_id, count in rows_raw.items():
        if not isinstance(org_id, str) or not org_id:
            raise ValueError(f"rows_by_org has a non-string org key: {org_id!r}")
        rows[org_id] = _as_int(count, f"rows_by_org[{org_id}]")
    total = _as_int(body.get("total_rows"), "total_rows")
    if total != sum(rows.values()):
        # A truncated or hand-edited reply. Publishing the stated total would
        # over- or under-state the window; publishing the sum would hide the
        # disagreement. FAIL the reading.
        raise ValueError(
            f"{ROW_GROWTH_RPC} reply is inconsistent: total_rows={total} but "
            f"rows_by_org sums to {sum(rows.values())}"
        )
    return AnalyticsRowGrowth(
        since=since,
        until=None if until is None else str(until),
        total_rows=total,
        rows_by_org=rows,
        ok=True,
        error=None,
        measured_at=measured_at or datetime.now(UTC).isoformat(),
    )


def read_analytics_row_growth(cp: Any, *, since: Any, until: Any = None) -> AnalyticsRowGrowth:
    """Per-org ``analytics_events`` rows in ``[since, until)`` through *cp*. TOTAL.

    ``until=None`` means open-ended (everything at or after ``since``). Both
    bounds accept a tz-aware ``datetime`` or an ISO-8601 string. Returns
    ``ok=False`` on any failure — a missing handle, a dead control plane, a
    bad bound, a malformed reply — and never raises.
    """
    measured_at = datetime.now(UTC).isoformat()
    since_str: str = "" if since is None else str(since)
    until_str: str | None = None if until is None else str(until)
    try:
        if cp is None:
            raise ValueError("no control-plane handle supplied")
        since_iso = _iso(since, "since")
        if since_iso is None:
            raise ValueError("since is required and must not be None")
        until_iso = _iso(until, "until")
        since_str, until_str = since_iso, until_iso
        payload = cp.rpc_value(ROW_GROWTH_RPC, {"p_since": since_iso, "p_until": until_iso})
        return parse_row_growth(payload, measured_at=measured_at)
    except Exception as e:
        _logger.debug("analytics row-growth measurement failed (non-fatal)", exc_info=True)
        # ``_iso`` may have failed before a normalised bound existed; report
        # the caller's own values rather than an empty string.
        if not since_str:
            since_str = str(since)
        return _failed_growth(since_str, until_str, measured_at, e)
