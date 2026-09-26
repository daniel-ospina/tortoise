"""Per-capture graph-operation accounting (#3359).

Why this exists
~~~~~~~~~~~~~~~
The product bills per-usage past a threshold number of **graph ops**, but the
only meter today counts **API calls** — ``metering.write_ops`` counts one
``capture_session`` as 1 write op while the capture physically issues ~440
FalkorDB operations (median; measured range 354-523 — the figure and its
phase split are stated in the ``tortoise/metering.py`` docstring, and the
per-session distribution is readable via ``capture_graph_ops_distribution()``).
The billed unit therefore undercounts physical work by roughly 440x and there
is no permanent graph-op data at all.

This module makes graph operations **countable per captured session**, split
read/write and attributed to the capture phase the op was issued in. It is
**measurement, not metering** — nothing here touches billing, quotas,
entitlements, or caps.

The choke point
~~~~~~~~~~~~~~~
Every SDK/capture graph access funnels through one place:
``tortoise.projection._GuardedGraph.query`` (the wrapper installed on
``FalkorProjection.g``). ``_GuardedGraph.query`` calls :func:`record_graph_op`
before delegating to the raw FalkorDB handle. When no capture is active the
call is a single ContextVar read and returns immediately (zero overhead on
the non-capture path).

Phases
~~~~~~
``PHASES`` — the four capture phases the op is attributed to:

* ``session_store`` — everything ``capture_session`` / the hosted capture
  pipeline issues directly (Session MERGE, the turn-store loop, Event/Source
  mint, provenance stamp, entity linking, receipt writes).
* ``extraction`` — READ ops issued inside the extraction call
  (``_extract_session_v2`` / ``_extract_session_llm``): S3 search, dedup,
  supersede resolution.
* ``commit`` — WRITE ops issued inside the extraction call: entities, points,
  events, operators, edges (the deterministic commit leg).
* ``belief`` — any op inside ``_apply_capture_ingest_ep`` (promotion +
  bounded local dream / belief propagation).

The extraction phase is entered with the single marker ``"extract"``; the
counter splits it into ``extraction``/``commit`` by the leading Cypher verb of
each op.

Known gap (explicit, not silently included or excluded)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
**Registry / control-plane graph handles are outside ``_GuardedGraph``** and
are therefore NOT counted. Metering records (``:MeteringRecord``), team
registry nodes, webhook events etc. live in the registry namespace and are
written through a different handle. The reported per-session figure is the
**data-plane capture cost**, not the full request cost. This is a known
under-count of the same sign as the billing gap; it is documented here so a
reader never mistakes the number for "all graph work in the request".

Concurrency
~~~~~~~~~~~
The active counter is a **mutable object referenced by a ContextVar**. The
hosted capture runs its extraction on a worker thread with
``contextvars.copy_context()`` (``_submit_off_loop``); a copied context shares
the *same* counter object, so mutations made in the worker are visible to the
caller when the worker returns. We therefore never rebind the ContextVar
inside a capture — we only mutate the object it points at.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import os
import re
import statistics
from contextlib import contextmanager
from contextvars import ContextVar

__all__ = [
    "PHASES",
    "GraphOpsCounter",
    "capture_graph_ops_distribution",
    "capture_phase",
    "classify_op",
    "count_graph_ops",
    "counts_capture_ops",
    "counts_capture_ops_async",
    "distribution_from_rows",
    "phased",
    "read_capture_graph_ops",
    "record_graph_op",
]

#: The capture phases an op can be attributed to, in report order.
PHASES: tuple[str, ...] = ("session_store", "extraction", "commit", "belief")

#: The internal marker used while inside the extraction call; split by verb.
_EXTRACT = "extract"

#: Every phase ``capture_phase`` accepts — the four report phases plus the
#: internal marker. Validated on entry, so a typo cannot create a bucket that
#: ``as_dict`` does not emit (which would break ``total == sum(by_phase)``).
_VALID_PHASES = frozenset(PHASES) | {_EXTRACT}

# Leading Cypher comments / whitespace are stripped before the verb is read.
_LEADING = re.compile(r"^(?:\s+|//[^\n]*\n|/\*.*?\*/)+", re.DOTALL)
_WRITE_VERBS = frozenset(
    {"CREATE", "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "DROP", "FOREACH"}
)


def classify_op(cypher: str) -> str:
    """Classify one Cypher statement as ``"read"`` or ``"write"``.

    Classification is by the **leading verb** of the first clause (after
    stripping leading whitespace and comments). ``CREATE``/``MERGE``/``SET``/
    ``DELETE``/``DETACH``/``REMOVE``/``DROP``/``FOREACH`` are writes; anything
    else (``MATCH``, ``RETURN``, ``CALL``, …) is a read. A ``MERGE`` is a
    write even when it ends up matching an existing node — it is the write
    path.

    Known limitation (faithful to the #3359 hand measurement, which
    classified the same way): a **chained** query whose first clause is a
    read but which then writes — e.g. ``MATCH ... MERGE (s)-[:CONTAINS]->(t)``
    or ``MATCH ... DETACH DELETE`` — is classified ``read`` by this rule. The
    measured baseline (~441 ops, phase percentages) was produced under this
    exact rule, so the counter stays comparable to it rather than silently
    changing the meaning of the split. The overall ``total`` is unaffected.
    """
    body = _LEADING.sub("", cypher, count=1)
    verb = re.match(r"[A-Za-z_]+", body)
    if verb is None:
        return "read"
    return "write" if verb.group(0).upper() in _WRITE_VERBS else "read"


class GraphOpsCounter:
    """Accumulates graph ops for ONE capture, split by phase and read/write."""

    __slots__ = ("by_phase",)

    def __init__(self) -> None:
        self.by_phase: dict[str, dict[str, int]] = {
            p: {"read": 0, "write": 0} for p in PHASES
        }

    def record(self, phase: str, kind: str) -> None:
        bucket = self.by_phase.get(phase)
        if bucket is None:
            # An unknown phase must not mint a bucket: ``total`` sums every
            # bucket but ``as_dict`` emits only PHASES, so a stray phase would
            # silently break ``total == sum(by_phase)`` in the emitted row.
            raise ValueError(
                f"unknown capture phase {phase!r}; expected one of {PHASES}")
        bucket[kind] += 1

    # ── derived views ──────────────────────────────────────────────────
    @property
    def total(self) -> int:
        return sum(b["read"] + b["write"] for b in self.by_phase.values())

    @property
    def reads(self) -> int:
        return sum(b["read"] for b in self.by_phase.values())

    @property
    def writes(self) -> int:
        return sum(b["write"] for b in self.by_phase.values())

    def as_dict(self) -> dict:
        """The stored shape: totals + read/write split + per-phase counts."""
        return {
            "total": self.total,
            "read": self.reads,
            "write": self.writes,
            "by_phase": {
                p: {"read": self.by_phase[p]["read"],
                    "write": self.by_phase[p]["write"],
                    "total": self.by_phase[p]["read"] + self.by_phase[p]["write"]}
                for p in PHASES
            },
        }


# ── context plumbing ──────────────────────────────────────────────────
_logger = logging.getLogger("tortoise.graph_ops")

_ACTIVE: ContextVar[GraphOpsCounter | None] = ContextVar(
    "tortoise_graph_ops_active", default=None)
_PHASE: ContextVar[str] = ContextVar(
    "tortoise_graph_ops_phase", default="session_store")


def record_graph_op(cypher: str) -> None:
    """Record one graph op against the active capture counter (if any).

    Called from ``_GuardedGraph.query`` on EVERY graph access. A no-op (one
    ContextVar read) when no capture is active, so non-capture paths pay
    nothing measurable.

    NEVER RAISES. The whole module is **measurement, not metering**, and this
    is the one function that runs INSIDE a customer's graph write — the
    caller's query has not executed yet, so anything raised here would abort a
    write that was otherwise perfectly valid. That is a metering failure
    causing data loss, which inverts the contract.

    The hazard is real rather than hypothetical: ``GraphOpsCounter.record``
    deliberately RAISES on an unknown phase (it must not mint a bucket
    ``as_dict`` omits). ``capture_phase`` validates before setting ``_PHASE``,
    so today the raise is unreachable — but ``_PHASE`` is a ContextVar any
    future caller could set directly, and a typo there would otherwise surface
    as a failed graph write rather than as a metering bug. Caught here, at the
    one place every caller passes through, rather than at each call site.

    Recorded, not silent: the drop is logged at WARNING with the traceback, so
    the measurement gap is discoverable instead of becoming a quietly low
    count in a figure this repo prices from.
    """
    counter = _ACTIVE.get()
    if counter is None:
        return
    try:
        kind = classify_op(cypher)
        phase = _PHASE.get()
        if phase == _EXTRACT:
            # Inside the extraction call: reads are the search/dedup leg,
            # writes are the deterministic commit leg.
            phase = "extraction" if kind == "read" else "commit"
        counter.record(phase, kind)
    except Exception:  # see the docstring: never break the write
        _logger.warning(
            "graph-op metering dropped one op (non-fatal) — the graph write "
            "it was measuring proceeds unmeasured",
            exc_info=True,
        )


@contextmanager
def count_graph_ops(counter: GraphOpsCounter | None = None):
    """Activate graph-op counting for the duration of a capture.

    Yields the :class:`GraphOpsCounter` that will hold the session's ops. The
    counter is a mutable object; nested captures on the same thread would
    shadow it, which is why capture is single-session (a capture never nests
    inside another capture).
    """
    counter = counter or GraphOpsCounter()
    token = _ACTIVE.set(counter)
    try:
        yield counter
    finally:
        _ACTIVE.reset(token)


@contextmanager
def capture_phase(phase: str):
    """Attribute ops issued in this block to ``phase``.

    ``phase`` is one of :data:`PHASES`, or the internal ``"extract"`` marker
    (whose ops are split into ``extraction``/``commit`` by verb). Restores the
    previous phase on exit, so nesting (belief inside extraction, if it ever
    happened) stays honest. An unknown phase RAISES rather than minting a
    bucket ``as_dict`` would not emit.
    """
    if phase not in _VALID_PHASES:
        raise ValueError(
            f"unknown capture phase {phase!r}; expected one of "
            f"{(*tuple(PHASES), _EXTRACT)}")
    token = _PHASE.set(phase)
    try:
        yield
    finally:
        _PHASE.reset(token)


def counts_capture_ops(fn):
    """Method decorator: count graph ops for the whole call and attach the
    result to the returned dict under ``"graph_ops"``.

    Used on ``TortoiseSDK.capture_session`` (the selfhost/CLI capture path).
    The hosted path emits the same accounting as an analytics row instead of
    mutating the HTTP response (see ``hosted_api._capture_session_impl``).

    ⚠️ **Replay asymmetry, stated so it is not discovered as a bug.** This
    decorator attaches ``graph_ops`` on EVERY ``capture_session`` call, a
    replay included (a replay is a real call that re-reads the graph). The
    hosted row is deliberately SUPPRESSED for a replay (it is not a new
    capture, so it is not a measured capture). Read a selfhost
    ``res["graph_ops"]`` as "ops this call issued", not "ops this session's
    first capture issued".
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with count_graph_ops() as counter:
            result = fn(*args, **kwargs)
        if isinstance(result, dict):
            result["graph_ops"] = counter.as_dict()
        return result

    return wrapper


def counts_capture_ops_async(fn):
    """Async counterpart of :func:`counts_capture_ops`.

    Runs the wrapped coroutine with graph-op counting active and injects the
    live :class:`GraphOpsCounter` as the keyword argument ``_graph_ops``, so
    the body can read the accounting itself and emit it (the hosted path
    publishes it as an analytics row rather than mutating the response).

    ContextVar changes made in this coroutine persist across its own awaits
    (same task context); the extraction worker receives a *copy* of the
    context, which shares the same counter object, so worker mutations land in
    the same counter the caller reads.
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        with count_graph_ops() as counter:
            kwargs["_graph_ops"] = counter
            return await fn(*args, **kwargs)

    return wrapper


def phased(phase: str):
    """Method/function decorator: attribute ops issued inside it to ``phase``.

    ``phase`` is a member of :data:`PHASES` or the internal ``"extract"``
    marker (split into ``extraction``/``commit`` by verb).
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with capture_phase(phase):
                return fn(*args, **kwargs)

        return wrapper

    return deco


# ── readable distribution (item 3) ────────────────────────────────────
def _percentile(sorted_vals: list[float], pct: float) -> float:
    """Nearest-rank percentile (no interpolation) — deterministic and honest
    for small capture populations."""
    if not sorted_vals:
        return 0.0
    k = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return sorted_vals[k - 1]


def _as_int(value) -> int:
    """Coerce a stored per-session field to ``int``, 0 on anything unusable.

    The reader's contract is to read *arbitrary* stored records, so a
    malformed field must skip that field, never abort the whole distribution.
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def distribution_from_rows(rows) -> dict:
    """Per-session graph-op distribution (median / p95 / totals).

    ``rows`` is any iterable of stored per-session records: either the
    **emitted** namespaced shape (``{"graph_ops_total": ..., "graph_ops_read":
    ..., "graph_ops_write": ..., "graph_ops_turns": ...}`` — the
    ``analytics_events.properties`` row) or the legacy flat shape
    (``{"total": ..., "read": ..., "write": ..., "turns": ...}``). Each row
    is canonicalised first, so the documented production read path (aggregate
    the Supabase props directly) and the JSONL fallback agree. Returns:

    ``{sessions, distinct_sessions, total_ops, median, p95, min, max,
       median_per_turn, read_total, write_total}``

    ⚠️ ``sessions`` counts ROWS (capture ATTEMPTS), the same way the sibling
    ``capture_cost`` row does. A capture that fails and is retried emits a
    second row with the same ``session_id``; use ``distinct_sessions`` when
    the question is "how many sessions", not "how many attempts".

    An empty input returns ``sessions=0`` and zeroes — never a fabricated
    number.
    """
    totals: list[float] = []
    rates: list[float] = []
    read_total = 0
    write_total = 0
    seen_sessions: set[str] = set()
    for raw in rows or []:
        row = _canonical_props(raw) if isinstance(raw, dict) else raw
        try:
            total = float(row.get("total", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        totals.append(total)
        sid = row.get("session_id") if isinstance(row, dict) else None
        if sid:
            seen_sessions.add(str(sid))
        try:
            turn_count = float(row.get("turns", 0) or 0)
        except (AttributeError, TypeError, ValueError):
            turn_count = 0.0
        # Per-session rate first, then the median of the rates — a ratio of
        # median-total to MEAN-turns is not a per-turn median (and is skewed
        # by one long session). A session with no turns contributes 0.
        rates.append(total / turn_count if turn_count > 0 else 0.0)
        read_total += _as_int(row.get("read"))
        write_total += _as_int(row.get("write"))
    if not totals:
        return {"sessions": 0, "distinct_sessions": 0, "total_ops": 0,
                "median": 0.0, "p95": 0.0, "min": 0.0, "max": 0.0,
                "median_per_turn": 0.0, "read_total": 0, "write_total": 0}
    ordered = sorted(totals)
    total_ops = int(sum(ordered))
    return {
        "sessions": len(ordered),
        "distinct_sessions": len(seen_sessions),
        "total_ops": total_ops,
        "median": statistics.median(ordered),
        "p95": _percentile(ordered, 95),
        "min": ordered[0],
        "max": ordered[-1],
        "median_per_turn": round(statistics.median(rates), 2) if rates else 0.0,
        "read_total": read_total,
        "write_total": write_total,
    }


def _canonical_props(props: dict) -> dict:
    """Map a stored ``capture_graph_ops`` row to the flat shape
    :func:`distribution_from_rows` reads.

    Accepts the emitted namespaced keys (``graph_ops_*``) and the legacy flat
    keys, so a row written before the rename still reads.
    """
    def pick(*names):
        for n in names:
            if n in props:
                return props[n]
        return None

    return {
        "session_id": props.get("session_id"),
        "total": pick("graph_ops_total", "total"),
        "read": pick("graph_ops_read", "read"),
        "write": pick("graph_ops_write", "write"),
        "turns": pick("graph_ops_turns", "turns"),
        "by_phase": pick("graph_ops_by_phase", "by_phase"),
    }


def read_capture_graph_ops(path: str | None = None) -> list[dict]:
    """Read stored ``capture_graph_ops`` rows from the local analytics JSONL
    fallback (``~/.tortoise/analytics_fallback.jsonl``).

    The production store is the Supabase ``analytics_events`` table; this
    helper covers the fallback path so the distribution is readable without
    credentials. Returns the ``properties`` of each ``capture_graph_ops``
    event, in file order.
    """
    if path is None:
        path = os.path.join(
            os.path.expanduser("~"), ".tortoise", "analytics_fallback.jsonl")
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event_name") == "capture_graph_ops":
                    props = rec.get("properties") or {}
                    if props:
                        rows.append(props)
    except FileNotFoundError:
        return []
    return rows


def capture_graph_ops_distribution(path: str | None = None) -> dict:
    """Readable per-session graph-op distribution from the fallback store.

    ``distribution_from_rows(read_capture_graph_ops(path))`` — the one-liner a
    human or a cron report runs. For the Supabase store, run the same shape of
    aggregation over ``analytics_events.properties`` where
    ``event_name = 'capture_graph_ops'``.
    """
    return distribution_from_rows(read_capture_graph_ops(path))
