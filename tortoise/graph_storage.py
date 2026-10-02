"""Per-graph (hence per-org) GRAPH STORAGE measurement, in MB (#5331).

WHY THIS EXISTS. The owner ruling (2026-09-26) is that storage is counted in
**MB/GB, not nodes** — for our cost input and eventually for customer-facing
billing. Postgres MB is scoped elsewhere; the graph MB unit was the gap:
``GRAPH.MEMORY USAGE`` is called by **zero** code (``git grep`` → empty).
This module closes that gap with a meter.

WHAT IT MEASURES. ``GRAPH.MEMORY USAGE <graph> SAMPLES <n>`` on a FalkorDB
graph handle. It returns **MB directly** and is **already per-graph**, so
per-org attribution comes free under one-graph-per-tenant: the reading's
``graph_name`` IS the attribution key, and the caller passes the org-scoped
handle.

⛔ THE TWO CAVEATS — they travel with EVERY reading, on the reading itself
--------------------------------------------------------------------------------
1. **It is a sampling-based ESTIMATE.** FalkorDB takes ``SAMPLES`` (default
   **100**, up to 10,000) and *averages* them; it is not an exact allocation.
2. **It EXCLUDES per-graph / Redis-key overhead** (the graph's registry entry
   and auxiliary keys are not in the figure).

⇒ So the meter reports a **range** (``min_mb``/``max_mb``/``spread_mb``), the
``SAMPLES`` count it used, and the number of ``repeats``, and it does not
claim a precision it does not have. **That is acceptable as a CAP INPUT; it is
NOT invoice-grade.** The sentence is carried verbatim on every reading
(:data:`PRECISION_NOTE`, ``GraphStorageReading.precision_note``) so a reader
cannot consume the number without it.

NOT A DIAL. Nothing here prices, caps, tiers, refuses or throttles. It is the
instrument, not the setting (#5331 is measurement only). The declared
``bytes_per_node`` constant and ``max_graph_nodes`` cap are owner territory and
are deliberately untouched.

FAIL-SOFT. :func:`measure_graph_storage` and :func:`record_graph_storage` are
TOTAL — never raise. A measurement fault must never fail a request or a write;
a failure is returned as a reading with ``ok=False`` and an ``error`` string
(and logged at DEBUG), never propagated.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

_logger = logging.getLogger(__name__)

__all__ = [
    "EXCLUDED_OVERHEAD",
    "PRECISION_NOTE",
    "SAMPLES_DEFAULT",
    "SAMPLES_MAX",
    "GraphStorageReading",
    "measure_and_record_graph_storage",
    "measure_graph_storage",
    "measure_projection_storage",
    "parse_memory_usage",
    "record_graph_storage",
]

#: FalkorDB's own default for ``SAMPLES`` (``GRAPH.MEMORY USAGE`` averages this
#: many nodes/edges). Kept in one place so the default the meter sends and the
#: default it reports cannot drift.
SAMPLES_DEFAULT = 100

#: FalkorDB's documented maximum for ``SAMPLES`` (up to 10,000). A request
#: above it is clamped, with a warning, rather than passed through.
SAMPLES_MAX = 10_000

# ⛔ This module's OWN bound, not a FalkorDB limit: ``repeats`` drives that many
# SYNCHRONOUS engine round trips, so an unbounded value is a hang (or a
# MemoryError building the totals tuple), not a measurement. The engine
# documents no maximum for the repeat COUNT — ``SAMPLES`` is the engine's own
# knob and has its own ceiling above — so this is ours, and it is deliberately
# modest: the repeats exist to produce a min/max RANGE, and a count beyond a
# couple of dozen is a typo, not a tighter estimate.
REPEATS_MAX = 32

#: What ``GRAPH.MEMORY USAGE`` does NOT include. The strings are part of the
#: reading (not only of this docstring) because a consumer that only ever sees
#: the number must still be able to read what it leaves out.
EXCLUDED_OVERHEAD: tuple[str, ...] = (
    "per-graph overhead (the graph's registry entry and per-graph metadata)",
    "Redis-key overhead (the graph key and auxiliary keys)",
)

#: The one sentence a reader must see. Carried on every reading (and in its
#: ``as_dict()``) so the estimate can never be consumed as an invoice figure.
PRECISION_NOTE = (
    "SAMPLING ESTIMATE, not an exact allocation (FalkorDB averages SAMPLES "
    "nodes/edges), and it EXCLUDES per-graph and Redis-key overhead. "
    "Acceptable as a CAP INPUT; NOT invoice-grade."
)


def _decode(value: Any) -> str:
    """Decode a redis reply token (bytes → str) for use as a dict key."""
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def _normalise_field(key: str, value: Any) -> Any:
    """Decode a nested flat key/value list into a dict; pass anything else through.

    FalkorDB delivers the per-label breakdown as a NESTED flat list. BOTH reply
    shapes must be normalised: a client that decoded the reply itself hands back
    a dict whose values may STILL hold that nested list, so normalising only the
    list branch left the advertised dict shape failing on a well-formed reply
    (``'list' object has no attribute 'items'`` downstream).
    """
    if isinstance(value, (list, tuple)):
        if len(value) % 2 != 0:
            raise ValueError(
                f"GRAPH.MEMORY USAGE nested field {key!r} is not a flat "
                f"key/value list (odd length {len(value)})")
        return {
            _decode(value[j]): value[j + 1]
            for j in range(0, len(value), 2)
        }
    return value


def parse_memory_usage(reply: Any) -> dict[str, Any]:
    """Parse a ``GRAPH.MEMORY USAGE`` reply into a dict.

    FalkorDB replies with a FLAT key/value list, where a per-label breakdown
    arrives as a NESTED flat list (``[label, mb, label, mb, …]``). Bytes keys
    are decoded. The result keeps the raw field names, so a reader can see the
    ``indices_sz_mb`` / ``amortized_node_attributes_by_label_sz_mb`` breakdown.

    RAISES :class:`ValueError` on a reply that is not that shape, or that
    carries no ``total_graph_sz_mb`` — a malformed reply must FAIL the reading
    rather than silently masquerade as a zero-byte graph (the honesty guard the
    meter's fail-soft path depends on).
    """
    if isinstance(reply, dict):
        # A client configured to decode the reply itself may hand back a dict
        # — whose nested values may still be flat lists, so normalise them.
        parsed = {
            _decode(k): _normalise_field(_decode(k), v)
            for k, v in reply.items()
        }
        if "total_graph_sz_mb" in parsed:
            return parsed
        raise ValueError(
            "GRAPH.MEMORY USAGE reply has no total_graph_sz_mb field")
    if not isinstance(reply, (list, tuple)):
        raise ValueError(
            f"GRAPH.MEMORY USAGE reply must be a flat key/value list, got "
            f"{type(reply).__name__}")
    if len(reply) % 2 != 0:
        raise ValueError(
            "GRAPH.MEMORY USAGE reply is not a flat key/value list "
            f"(odd length {len(reply)})")
    parsed: dict[str, Any] = {}
    for i in range(0, len(reply), 2):
        key = _decode(reply[i])
        value = reply[i + 1]
        if isinstance(value, (list, tuple)):
            value = _normalise_field(key, value)
        parsed[key] = value
    if "total_graph_sz_mb" not in parsed:
        raise ValueError(
            "GRAPH.MEMORY USAGE reply has no total_graph_sz_mb field")
    return parsed


def _resolve_samples(value: Any) -> int:
    """Validate ``SAMPLES`` to ``[1, SAMPLES_MAX]``.

    A non-integer RAISES (the caller's input is wrong and must surface as a
    failed reading, not a silently-different measurement). Out-of-range whole
    numbers are CLAMPED with a warning — a value below 1 or above FalkorDB's
    documented max is a typo, and clamping keeps the reading honest about what
    was actually run.
    """
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        # OverflowError is NOT academic: ``int(float("inf"))`` (and
        # ``int(Decimal("Infinity"))``) raises it, and this function's contract
        # is that a bad input FAILS SOFT — it must never propagate a raise into
        # a request. NaN arrives as ValueError, +-inf as OverflowError.
        raise ValueError(f"SAMPLES must be an integer, got {value!r}") from None
    if n != value:
        # A FINITE NON-INTEGER must not be silently FLOORED (``int(5.9) == 5``):
        # the caller's input is wrong, and a floored count would report a
        # precision the run never had — exactly the "silently-different
        # measurement" this docstring says is rejected. ``n != value`` accepts
        # whole floats (5.0) and Decimal("5") while rejecting 5.9.
        raise ValueError(
            f"SAMPLES must be a whole number, got {value!r}") from None
    if n < 1:
        _logger.warning(
            "graph storage: SAMPLES=%r is below 1 — clamping to 1", value)
        return 1
    if n > SAMPLES_MAX:
        _logger.warning(
            "graph storage: SAMPLES=%r exceeds FalkorDB's max %d — clamping "
            "to %d", value, SAMPLES_MAX, SAMPLES_MAX)
        return SAMPLES_MAX
    return n


def _resolve_repeats(value: Any) -> int:
    """Validate the repeat count to a whole number in ``[1, REPEATS_MAX]``.

    Out-of-range values are CLAMPED with a warning, matching ``SAMPLES`` — the
    upper bound matters here because every repeat is a synchronous engine round
    trip, so an unbounded count is a hang rather than a measurement.
    """
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        # See ``_resolve_samples``: ``int(inf)`` raises OverflowError, and a
        # raise here would escape ``measure_graph_storage``'s fail-soft contract.
        raise ValueError(
            f"repeats must be an integer, got {value!r}") from None
    if n != value:
        raise ValueError(
            f"repeats must be a whole number, got {value!r}") from None
    if n < 1:
        _logger.warning(
            "graph storage: repeats=%r is below 1 — clamping to 1", value)
        return 1
    if n > REPEATS_MAX:
        _logger.warning(
            "graph storage: repeats=%r exceeds this module's max %d — clamping "
            "to %d", value, REPEATS_MAX, REPEATS_MAX)
        return REPEATS_MAX
    return n


@dataclass(frozen=True)
class GraphStorageReading:
    """One per-graph storage reading, with its honesty caveats attached.

    ``total_mb`` is the best estimate (the MEDIAN of the per-repeat totals);
    ``min_mb``/``max_mb``/``spread_mb`` are the observed range across those
    repeats, and ``readings_mb`` keeps every individual total. ``samples`` is
    the ``SAMPLES`` value sent to FalkorDB; ``repeats`` is how many times the
    command ran.

    ``estimated`` is always True (the command is a sampling estimate) and
    ``excludes``/``precision_note`` carry the two caveats so they cannot be
    separated from the number.
    """

    graph_name: str
    total_mb: float
    samples: int
    repeats: int
    readings_mb: tuple[float, ...]
    min_mb: float
    max_mb: float
    spread_mb: float
    indices_mb: float | None
    node_attributes_mb: dict[str, float] = field(default_factory=dict)
    ok: bool = True
    error: str | None = None
    measured_at: str = ""
    estimated: bool = True
    excludes: tuple[str, ...] = EXCLUDED_OVERHEAD
    precision_note: str = PRECISION_NOTE

    def as_dict(self) -> dict[str, Any]:
        """Serialize the reading — caveats INCLUDED, so they travel with it.

        The ``graph_storage_*`` keys are the ledger/response vocabulary; a
        reader that only renders those keys still receives ``estimated``,
        ``excludes`` and ``precision_note``.
        """
        return {
            "graph_name": self.graph_name,
            "graph_storage_mb": self.total_mb,
            "graph_storage_indices_mb": self.indices_mb,
            "graph_storage_node_attributes_mb": dict(self.node_attributes_mb),
            "graph_storage_samples": self.samples,
            "graph_storage_repeats": self.repeats,
            "graph_storage_readings_mb": list(self.readings_mb),
            "graph_storage_min_mb": self.min_mb,
            "graph_storage_max_mb": self.max_mb,
            "graph_storage_spread_mb": self.spread_mb,
            "graph_storage_estimated": self.estimated,
            "graph_storage_excludes": list(self.excludes),
            "graph_storage_precision_note": self.precision_note,
            "graph_storage_measured_at": self.measured_at,
            "graph_storage_ok": self.ok,
            "graph_storage_error": self.error,
        }


def _failed_reading(graph_name: str, samples: int, repeats: int,
                    measured_at: str, error: Any) -> GraphStorageReading:
    """A total, zero-valued reading carrying the failure — never raises."""
    return GraphStorageReading(
        graph_name=graph_name,
        total_mb=0.0,
        samples=samples,
        repeats=repeats,
        readings_mb=(),
        min_mb=0.0,
        max_mb=0.0,
        spread_mb=0.0,
        indices_mb=None,
        node_attributes_mb={},
        ok=False,
        error=str(error)[:300],
        measured_at=measured_at,
    )


def measure_graph_storage(client: Any, graph_name: Any, *,
                          samples: Any = SAMPLES_DEFAULT,
                          repeats: Any = 1) -> GraphStorageReading:
    """Read ``GRAPH.MEMORY USAGE`` for *graph_name* through *client*.

    *client* is the FalkorDB/redis client (``proj.db``) — anything exposing
    ``execute_command("GRAPH.MEMORY", "USAGE", <graph>, "SAMPLES", <n>)``.
    *graph_name* is the per-tenant graph (the org attribution key).

    ``repeats`` runs the command several times so the reading can report an
    observed range; the default 1 costs exactly one command. The point estimate
    and the per-label breakdown are taken from the SAME repeat — the
    nearest-rank median (lower-middle for an even count) — so the breakdown can
    never contradict the total it is a share of. They are identical for the
    default ``repeats=1``.

    TOTAL: returns a reading with ``ok=False`` on any failure — a missing
    handle, an engine that does not implement the command (embedded FalkorDBLite
    may not), a malformed reply, or a socket error. Never raises.
    """
    measured_at = datetime.now(UTC).isoformat()
    gname = "" if graph_name is None else str(graph_name)
    try:
        resolved_samples = _resolve_samples(samples)
        resolved_repeats = _resolve_repeats(repeats)
    except ValueError as e:
        _logger.debug("graph storage measurement rejected its inputs: %s", e)
        return _failed_reading(gname, SAMPLES_DEFAULT, 1, measured_at, e)

    if client is None or not gname:
        return _failed_reading(
            gname, resolved_samples, resolved_repeats, measured_at,
            "no graph handle / graph name supplied")

    try:
        parses: list[dict[str, Any]] = []
        for _ in range(resolved_repeats):
            reply = client.execute_command(
                "GRAPH.MEMORY", "USAGE", gname, "SAMPLES", resolved_samples)
            parses.append(parse_memory_usage(reply))
        totals = tuple(float(p["total_graph_sz_mb"]) for p in parses)
        if not all(math.isfinite(t) for t in totals):
            # A NaN/inf total is a MALFORMED reply, not a measurement: it must
            # FAIL the reading so the ledger never records a fabricated zero
            # (see ``record_graph_storage``).
            raise ValueError(
                "GRAPH.MEMORY USAGE returned a non-finite total_graph_sz_mb: "
                f"{totals!r}")
        # The DERIVED spread needs the same guard, and the per-total check above
        # does NOT cover it: subtracting two finite extremes can overflow to inf
        # (e.g. 1e308 and -1e308), which is the identical failure this
        # sanitisation exists to prevent — an un-serialisable field reaching a
        # strict JSON encoder. ``json.dumps(..., allow_nan=False)`` raises, so a
        # consumer gets a 500 instead of a measurement. A total so large it
        # cannot be ranged is a malformed reply, so this FAILS the reading
        # rather than publishing an infinite spread.
        spread = max(totals) - min(totals)
        if not math.isfinite(spread):
            raise ValueError(
                "GRAPH.MEMORY USAGE totals span a non-finite range: "
                f"min={min(totals)!r} max={max(totals)!r} -> spread={spread!r}")
        # NEAREST-RANK median (lower-middle for an even count), not
        # ``statistics.median``: the point estimate and the breakdown MUST come
        # from the SAME repeat, or an averaged total can contradict its own
        # index share (e.g. total=3.0 with indices=9.0).
        order = sorted(range(len(totals)), key=lambda i: totals[i])
        chosen = order[(len(totals) - 1) // 2]
        point = totals[chosen]
        source = parses[chosen]
        # ⛔ SANITISE AT THE SOURCE, not only at the ledger boundary. A
        # non-finite index share must be ABSENT, never NaN: NaN is a THIRD state
        # the nullable column's contract does not admit (it means "the engine
        # did not report an index share", and the ledger stores NULL), it
        # violates the module's own invariant (``nan <= total`` is False, so
        # "the index share never exceeds the total" stops holding), and it makes
        # the reading un-serialisable — ``json.dumps(as_dict(),
        # allow_nan=False)`` raises, so the first consumer that returns this dict
        # as JSON yields a 500 instead of a measurement. The TOTAL and the
        # DERIVED spread are both held to this standard above; the index share
        # must not be weaker.
        indices = source.get("indices_sz_mb")
        if indices is not None:
            indices = float(indices)
            if not math.isfinite(indices) or indices > point:
                # A share that EXCEEDS the point estimate is a malformed reply:
                # the module's invariant is that the index share is a PART of
                # the total, and the comment above justifies dropping NaN by
                # citing exactly that. Enforcing it here keeps the claim a
                # consumer contract instead of a hope — a finite 9.0 against a
                # total of 2.0 is dropped for the same reason a NaN is: it is
                # not a usable share. The TOTAL is unaffected and stays `ok`.
                indices = None
        raw_attrs = source.get("amortized_node_attributes_by_label_sz_mb") or {}
        # Same standard per label: a non-finite value is NOT a measurement, so
        # the label is absent from the breakdown rather than reported as NaN or
        # as a fabricated 0.0 (which would claim an exactly-zero attribute cost).
        attrs: dict[str, float] = {}
        for label, value in raw_attrs.items():
            share = float(value)
            if math.isfinite(share):
                attrs[str(label)] = share
        return GraphStorageReading(
            graph_name=gname,
            total_mb=point,
            samples=resolved_samples,
            repeats=resolved_repeats,
            readings_mb=totals,
            min_mb=min(totals),
            max_mb=max(totals),
            spread_mb=spread,
            indices_mb=indices,
            node_attributes_mb=attrs,
            ok=True,
            error=None,
            measured_at=measured_at,
        )
    except Exception as e:
        _logger.debug(
            "graph storage measurement failed for %s (non-fatal)", gname,
            exc_info=True)
        return _failed_reading(
            gname, resolved_samples, resolved_repeats, measured_at, e)


def measure_projection_storage(proj: Any, *,
                               samples: Any = SAMPLES_DEFAULT,
                               repeats: Any = 1) -> GraphStorageReading:
    """Measure an OPEN :class:`~tortoise.projection.FalkorProjection`.

    Reads ``proj.db`` (the client) and ``proj.graph_name`` (the per-org graph).

    ⛔ CALLER'S RESPONSIBILITY — ``FalkorProjection.__init__`` calls
    ``_ensure_indexes()`` UNCONDITIONALLY, so *opening* a handle can issue DDL
    (and can build an index over the whole graph). This function opens NOTHING:
    pass an ALREADY-OPEN projection, so a measurement never pays that cost and
    never issues schema work of its own. A handle missing either attribute
    yields a failed reading, not a crash.
    """
    return measure_graph_storage(
        getattr(proj, "db", None), getattr(proj, "graph_name", None),
        samples=samples, repeats=repeats)


def record_graph_storage(org_id: str | None, reading: GraphStorageReading | None,
                         *, _selfhost_transport: bool = False) -> dict | None:
    """Persist *reading* on the per-org metering ledger. TOTAL — never raises.

    A GAUGE SET (the last reading in the window wins), not an increment: graph
    storage is a current-state measurement, not work that accumulates. An
    ``ok=False`` reading is NOT written — a failed measurement must not appear
    on the ledger as a zero-byte graph (the zero would be a lie, not a
    measurement).

    The failure is reported to the operator through the LOGGER at WARNING — a
    dropped ledger write is operator-relevant (a graph that stops being billed
    looks identical to a quiet one), so a level dropped at production settings
    would make this claim false. The ledger write itself is best-effort
    (mirrors every other metering lane).
    """
    if not org_id or reading is None:
        return None
    if not reading.ok:
        _logger.warning(
            "graph storage reading for %s failed (%s) — not writing a zero to "
            "the ledger", org_id, reading.error)
        return None
    try:
        from tortoise import metering
        return metering.record_graph_storage_reading(
            org_id,
            total_mb=reading.total_mb,
            indices_mb=reading.indices_mb,
            samples=reading.samples,
            repeats=reading.repeats,
            min_mb=reading.min_mb,
            max_mb=reading.max_mb,
            spread_mb=reading.spread_mb,
            measured_at=reading.measured_at,
            _selfhost_transport=_selfhost_transport,
        )
    except Exception:
        _logger.warning(
            "graph storage ledger write failed for %s (non-fatal)", org_id,
            exc_info=True)
        return None


def measure_and_record_graph_storage(
        proj: Any, org_id: str | None, *,
        samples: Any = SAMPLES_DEFAULT,
        repeats: Any = 1,
        _selfhost_transport: bool = False) -> GraphStorageReading:
    """Measure an open projection and record it for *org_id* (both fail-soft).

    The end-to-end entry point: one call produces the reading AND puts it on
    the per-org ledger. Returns the reading in every case, so the caller can
    inspect what was measured. The return value does NOT report the ledger
    write: a dropped write is signalled to the operator by ``record_graph_storage``
    logging at WARNING, not by a changed return value.
    """
    reading = measure_projection_storage(
        proj, samples=samples, repeats=repeats)
    if reading.ok:
        record_graph_storage(
            org_id, reading, _selfhost_transport=_selfhost_transport)
    return reading
