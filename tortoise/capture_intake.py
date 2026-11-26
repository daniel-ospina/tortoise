"""#14342: the capture INTAKE queue — accepted work that is not yet processed.

## The defect this module removes

`POST /v1/sessions` reserves one of `hosted_api._CAPTURE_MAX_IN_FLIGHT` capture
slots at ADMISSION and **refuses** the request over that ceiling:

    {"code":"capture_capacity_saturated","in_flight":8,"limit":8,"retry_after":30}   (429)

Every refusal pushed the turn back onto the client, and the client's second
producer (`agent-infra/extensions/reflect-hook.ts`) parked a recovery copy in
`~/.tortoise/session-events/` — a folder nothing ever drained: 106 MB / 22
day-files / 17 894 records, measured on the owner's box 2026-11-25 (#14342).
That folder is not a duplicate store; **it is the failure parking lot**, and its
size is the size of the refusal.

For a background memory product, refusing an agent's turn is the one thing that
must never happen: the agent has already moved past the turn and cannot retry it.

## What replaces it

Accept, then queue. A capture over the ceiling gets a durable `:CaptureIntake`
row holding its exact payload, and an answer. The row lands in the TENANT'S
GRAPH — the same substrate `batch_ingest.GraphBatchStore` uses — and it is
written through the projection's own handle.

⛔ **WHAT THAT DOES *NOT* BUY, SAID PLAINLY (#14707 review P1).** The record is
the system of record and the graph is its derived hot cache
(`docs/architecture/STORAGE-ARCHITECTURE.md` §2.0), and a journal-backed graph
replay rebuilds `:CaptureIntake` from NOTHING: no journal record describes this
node, so `tortoise-rebuild` (a documented recovery path) DROPS every queued row
while the client has already pruned its spool entry on our 2xx. That is real
loss, and it is the same residual `:Batch` rows carry today — it is stated here,
in the plan's D1, and tracked as its own issue (`#14712`) rather than described
as "durable by the same rule that makes a capture durable", which was FALSE:
a capture is durable because the RECORD holds it.

What the row does buy, and what the door relies on: it survives the ACCEPTING
PROCESS (a restart, a deploy, a drain task that dies), which is the failure the
client's in-line retry could not survive. Nothing here claims more.

The drain re-enters the SAME capture
path (`_capture_session_impl`) rather than growing a second extraction
implementation (EXTRACTOR-V4-ARCHITECTURE's "extraction is the only producer of
the logical model").

The full map, the alternatives and the recorded decisions this obeys are in
`docs/architecture/CAPTURE-INGEST-PLAN.md`.

## The four invariants, all enforced HERE rather than by a caller

1. ⛔ **ASSUME-ACCEPTED MEANS RECORDED.** `accept()` returns only after the row
   exists in the store. A failure to write is RAISED — never swallowed into a
   would-be success — because the caller answers 2xx on the strength of it. The
   client treats any 2xx as filed and may prune its spool entry, so a row that
   did not land is a destroyed turn (#10479).
2. ⛔ **NO DEPTH CEILING EVER DROPS WORK.** `CAPTURE_QUEUE_MAX_DEPTH` is a
   **PUBLISHING** threshold, not a refusal and not a pacer: at or above it the
   queue still accepts and the condition is published (`stats()["depth"]`,
   `["over_depth"]`). What bounds intake GROWTH is the door's per-org ingest
   window (`commit_ingest_admit`, charged before the accept — see
   `hosted_api._capture_accept_into_intake`'s caller), not this number. The
   ceiling exists so an operator can SEE a stuck drain instead of discovering
   it from a 978 MB folder.
3. ⛔ **A CLAIMED ITEM IS NEVER LOST BY THE CLAIM.** Claiming is a state
   transition inside one statement (`queued` → `extracting`); an item whose
   worker died is reclaimed by `reclaim_stale()` after
   `CAPTURE_INTAKE_RECLAIM_AFTER_S` — the same lease shape the drain claim uses
   on the client (`tortoise/drain_claim.py`), and for the same reason: a crash
   must not convert an accepted turn into a missing one.
4. ⛔ **EXHAUSTION IS NOT DELETION.** After `CAPTURE_INTAKE_MAX_ATTEMPTS` the
   item moves to `failed` and STAYS in the store with its last error. It is a
   visible, listable, actionable row — the alternative (delete) is the silent
   loss this epic exists to end.

## What this module deliberately does NOT do

* It does not extract, embed, or write memory. The payload is handed back
  verbatim to the capture path; this module has no opinion about its content.
* It does not delete anything on `accept`, `claim` or `fail`. `complete()` is the
  only deletion, and it runs only after the capture path reported a terminal
  outcome.
* It stores NO credential. The row carries `org_id` and the resolved graph name
  only — the worker re-resolves the tenant through the API's own tenancy
  resolver, so no key, token or hash is ever copied to disk or to a graph.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

__all__ = [
    "CAPTURE_INTAKE_LABEL",
    "CAPTURE_INTAKE_MAX_ATTEMPTS",
    "CAPTURE_INTAKE_RECLAIM_AFTER_S",
    "CAPTURE_QUEUE_MAX_DEPTH",
    "CaptureIntake",
    "CaptureIntakeItem",
    "GraphIntakeStore",
    "IntakeCounters",
    "IntakeState",
    "IntakeStoreUnavailable",
    "MemoryIntakeStore",
    "item_id_for",
]


#: The record label of one accepted-but-unprocessed capture. A dedicated label
#: (rather than a flag on `:Session`) because the queue's lifecycle is NOT the
#: session's: a session can be captured once and queued many times (a TRUE
#: retry), and a queued item can be dropped after its session was already
#: recorded — the two must be countable independently.
CAPTURE_INTAKE_LABEL = "CaptureIntake"

#: How many times a drain may attempt one item before it is parked as `failed`.
#: Small on purpose: the capture path's own retry semantics are `capture_key`
#: idempotency plus the #2335 TRUE-retry lane, so an item that fails this many
#: times is a systemic fault (a broken graph handle, a bad payload), not
#: transient contention. Parking it keeps it VISIBLE instead of consuming the
#: drain forever.
CAPTURE_INTAKE_MAX_ATTEMPTS = 5

#: How long a claim may sit before another drain may take it over. Generous:
#: one capture is ~19 s of model calls on a 4-core box, and the reclaim window
#: must exceed the longest legitimate capture by a wide margin or a healthy
#: drain's own item would be stolen mid-flight.
CAPTURE_INTAKE_RECLAIM_AFTER_S = 900.0

#: The OVER-DEPTH publishing threshold (see invariant 2). Not a refusal, not a
#: drop, and NOT a pacer: intake growth is bounded by the door's per-org ingest
#: window, which is charged before an accept is written.
CAPTURE_QUEUE_MAX_DEPTH = 512

#: Attempts are stored per item; this backoff spaces a retry out so a
#: persistently failing item cannot spin the drain. Mirrors `batch_ingest`'s
#: ceiling shape rather than inventing a second schedule.
_RETRY_BASE_S = 15.0
_RETRY_MAX_S = 600.0


class IntakeState(StrEnum):
    """The item's state. There are no other states — a state machine with an
    unlisted member is how a row becomes invisible to every query."""

    QUEUED = "queued"
    EXTRACTING = "extracting"
    FAILED = "failed"


class IntakeStoreUnavailable(RuntimeError):
    """The store could not be read or written.

    ⛔ Raised, never swallowed on the accept path: `accept()` returning an item
    is the CALLER's licence to answer 2xx. A store that cannot write must make
    the door refuse with a retryable 503, which keeps the turn on the client
    (the safe direction) instead of acknowledging work that was never stored.
    """


def item_id_for(now: float | None = None) -> str:
    """A FIFO-sortable, collision-free item id.

    ⛔ The ordering IS the id. `claim_next` orders by `item_id` and nothing else,
    so an id that does not sort by arrival time would make the queue LIFO under
    load — and a LIFO intake starves the oldest turn, which is the opposite of
    "the head falls monotonically" (D1's capstone).
    """
    ts = time.time() if now is None else now
    return f"ci_{int(ts * 1000):013d}_{uuid.uuid4().hex[:12]}"


@dataclass(frozen=True)
class CaptureIntakeItem:
    """One accepted capture. `payload` is the request body VERBATIM."""

    item_id: str
    org_id: str
    graph: str | None
    session_id: str
    payload: dict
    enqueued_at: float
    attempts: int = 0
    state: str = IntakeState.QUEUED.value
    last_error: str | None = None

    def as_row(self) -> dict:
        """The wire/db shape (payload JSON-encoded)."""
        return {
            "item_id": self.item_id,
            "org_id": self.org_id,
            "graph": self.graph,
            "session_id": self.session_id,
            "payload": json.dumps(self.payload, separators=(",", ":")),
            "enqueued_at": float(self.enqueued_at),
            "attempts": int(self.attempts),
            "state": str(self.state),
            "last_error": self.last_error,
        }


def _item_from_row(row: Mapping[str, Any]) -> CaptureIntakeItem:
    raw = row.get("payload")
    if isinstance(raw, str):
        payload = json.loads(raw)
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:  # pragma: no cover - a corrupt row is surfaced by the caller
        raise IntakeStoreUnavailable("intake row carries no usable payload")
    return CaptureIntakeItem(
        item_id=str(row["item_id"]),
        org_id=str(row["org_id"]),
        graph=(str(row["graph"]) if row.get("graph") is not None else None),
        session_id=str(row["session_id"]),
        payload=payload,
        enqueued_at=float(row.get("enqueued_at") or 0.0),
        attempts=int(row.get("attempts") or 0),
        state=str(row.get("state") or IntakeState.QUEUED.value),
        last_error=(str(row["last_error"])
                    if row.get("last_error") is not None else None),
    )


class IntakeStore(Protocol):
    """The storage seam. One implementation per substrate, no business logic."""

    def put(self, item: CaptureIntakeItem) -> None: ...

    def claim_next(self, now: float) -> CaptureIntakeItem | None: ...

    def complete(self, item_id: str) -> None: ...

    def release(self, item_id: str, *, error: str, now: float,
                next_attempt_at: float | None,
                terminal: bool, refund_attempt: bool = False) -> None: ...

    def depth(self) -> int: ...

    def oldest_queued_at(self) -> float | None: ...

    def reclaim_stale(self, now: float,
                      after_s: float = CAPTURE_INTAKE_RECLAIM_AFTER_S) -> int: ...

    def rows(self, *, state: str | None = None,
             limit: int = 100) -> list[CaptureIntakeItem]: ...

    def queued_for_session(self,
                           session_id: str) -> CaptureIntakeItem | None: ...


class MemoryIntakeStore:
    """Process-local store — tests and single-process dev only.

    ⛔ NOT the production store, and the difference is the whole point of the
    module: a queue held in RAM is lost on restart, which is the exact class of
    loss the intake exists to remove. Production wires `GraphIntakeStore`.
    """

    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    def put(self, item: CaptureIntakeItem) -> None:
        with self._lock:
            self._rows[item.item_id] = item.as_row()

    def claim_next(self, now: float) -> CaptureIntakeItem | None:
        with self._lock:
            for item_id in sorted(self._rows):
                row = self._rows[item_id]
                if row["state"] != IntakeState.QUEUED.value:
                    continue
                if float(row.get("next_attempt_at") or 0.0) > now:
                    continue
                row["state"] = IntakeState.EXTRACTING.value
                row["attempts"] = int(row.get("attempts") or 0) + 1
                row["claimed_at"] = now
                return _item_from_row(row)
            return None

    def complete(self, item_id: str) -> None:
        with self._lock:
            self._rows.pop(item_id, None)

    def release(self, item_id: str, *, error: str, now: float,
                next_attempt_at: float | None, terminal: bool,
                refund_attempt: bool = False) -> None:
        with self._lock:
            row = self._rows.get(item_id)
            if row is None:
                return
            row["state"] = (IntakeState.FAILED.value if terminal
                            else IntakeState.QUEUED.value)
            row["last_error"] = error
            row["claimed_at"] = None
            row["next_attempt_at"] = next_attempt_at
            if refund_attempt:
                row["attempts"] = max(0, int(row.get("attempts") or 0) - 1)

    def depth(self) -> int:
        with self._lock:
            return sum(1 for r in self._rows.values()
                       if r["state"] == IntakeState.QUEUED.value)

    def oldest_queued_at(self) -> float | None:
        with self._lock:
            envs = [float(r["enqueued_at"]) for r in self._rows.values()
                    if r["state"] == IntakeState.QUEUED.value]
            return min(envs) if envs else None

    def reclaim_stale(self, now: float,
                      after_s: float = CAPTURE_INTAKE_RECLAIM_AFTER_S) -> int:
        """The same lease reclaim the graph store does (see its docstring).

        Present on BOTH stores: a drain that only survives a kill on one
        substrate is a drain whose kill-survival depends on configuration.
        """
        cutoff = float(now) - float(after_s)
        with self._lock:
            reclaimed = 0
            for row in self._rows.values():
                if row["state"] != IntakeState.EXTRACTING.value:
                    continue
                claimed_at = row.get("claimed_at")
                if claimed_at is None or float(claimed_at) > cutoff:
                    continue
                row["state"] = IntakeState.QUEUED.value
                row["claimed_at"] = None
                row["next_attempt_at"] = None
                row["last_error"] = ("previous drain stopped before this item "
                                     "finished (claim reclaimed)")
                reclaimed += 1
            return reclaimed

    def rows(self, *, state: str | None = None,
             limit: int = 100) -> list[CaptureIntakeItem]:
        with self._lock:
            out = [
                _item_from_row(r) for _, r in sorted(self._rows.items())
                if state is None or r["state"] == state
            ]
            return out[:limit]

    def queued_for_session(
            self, session_id: str) -> CaptureIntakeItem | None:
        with self._lock:
            for _, row in sorted(self._rows.items()):
                if row["session_id"] != session_id:
                    continue
                if row["state"] == IntakeState.FAILED.value:
                    continue
                return _item_from_row(row)
            return None


#: One statement does the claim, so two workers cannot both win it. FalkorDB
#: (and Neo4j) execute a single `MATCH … SET … RETURN` atomically per row;
#: a read-then-write pair would let both see `queued`.
_CLAIM_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL}) "
    "WHERE i.state = $queued AND (i.next_attempt_at IS NULL OR i.next_attempt_at <= $now) "
    "WITH i ORDER BY i.item_id ASC LIMIT 1 "
    "SET i.state = $extracting, i.claimed_at = $now, "
    "    i.attempts = coalesce(i.attempts, 0) + 1 "
    "RETURN i.item_id, i.org_id, i.graph, i.session_id, i.payload, "
    "       i.enqueued_at, i.attempts, i.state, i.last_error"
)

_PUT_CYPHER = (
    f"MERGE (i:{CAPTURE_INTAKE_LABEL} {{item_id: $item_id}}) "
    "ON CREATE SET i.org_id = $org_id, i.graph = $graph, "
    "              i.session_id = $session_id, i.payload = $payload, "
    "              i.enqueued_at = $enqueued_at, i.attempts = $attempts, "
    "              i.state = $state, i.last_error = $last_error "
    "RETURN i.item_id"
)

_COMPLETE_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL} {{item_id: $item_id}}) DETACH DELETE i"
)

_RELEASE_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL} {{item_id: $item_id}}) "
    "SET i.state = $state, i.last_error = $error, i.claimed_at = null, "
    "    i.next_attempt_at = $next_attempt_at, "
    "    i.attempts = CASE WHEN $refund AND coalesce(i.attempts, 0) > 0 "
    "                      THEN coalesce(i.attempts, 0) - 1 "
    "                      ELSE coalesce(i.attempts, 0) END "
    "RETURN i.item_id"
)

_DEPTH_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL}) WHERE i.state = $queued "
    "RETURN count(i)"
)

_OLDEST_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL}) WHERE i.state = $queued "
    "RETURN min(i.enqueued_at)"
)

_ROWS_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL}) "
    "WHERE $state IS NULL OR i.state = $state "
    "RETURN i.item_id, i.org_id, i.graph, i.session_id, i.payload, "
    "       i.enqueued_at, i.attempts, i.state, i.last_error "
    "ORDER BY i.item_id ASC LIMIT $limit"
)

#: `reclaim_stale`: an item claimed by a worker that died is `extracting`
#: forever unless something takes it back. Same lease shape as the client's
#: drain claim (`tortoise/drain_claim.py`), same reason.
_RECLAIM_STALE_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL}) "
    "WHERE i.state = $extracting AND i.claimed_at IS NOT NULL "
    "  AND i.claimed_at <= $cutoff "
    "SET i.state = $queued, i.claimed_at = null, i.next_attempt_at = null, "
    "    i.last_error = $error "
    "RETURN count(i)"
)


class GraphIntakeStore:
    """The durable store: one `:CaptureIntake` node per accepted capture.

    `query` is injected — `(cypher, params) -> result_set` — so this class holds
    no handle and no tenancy of its own (the caller owns both, exactly as
    `tortoise/batch_ingest.GraphBatchStore` does). The row lands in the org's
    RECORD graph, which is what makes the hand-off durable under
    STORAGE-ARCHITECTURE §2.0 rather than under "the process stayed up".

    ⛔ Nothing here reads or writes a credential. `org_id` and the resolved graph
    name are the only tenancy facts stored; the drain re-resolves the tenant
    through the API's own resolver.
    """

    def __init__(self, query: Callable[..., Iterable], *,
                 label: str = CAPTURE_INTAKE_LABEL) -> None:
        self._query = query
        self.label = label

    def _run(self, cypher: str, params: Mapping[str, Any]) -> list:
        try:
            result = self._query(cypher, params=dict(params))
        except Exception as exc:  # noqa: BLE001, RUF100 - normalised to ONE fault class
            raise IntakeStoreUnavailable(
                f"capture intake store query failed: {type(exc).__name__}: {exc}"
            ) from exc
        if result is None:
            return []
        return list(result)

    def put(self, item: CaptureIntakeItem) -> None:
        self._run(_PUT_CYPHER, item.as_row())

    def claim_next(self, now: float) -> CaptureIntakeItem | None:
        rows = self._run(_CLAIM_CYPHER, {
            "queued": IntakeState.QUEUED.value,
            "extracting": IntakeState.EXTRACTING.value,
            "now": float(now),
        })
        if not rows:
            return None
        return _item_from_row(_row_dict(rows[0]))

    def complete(self, item_id: str) -> None:
        self._run(_COMPLETE_CYPHER, {"item_id": item_id})

    def release(self, item_id: str, *, error: str, now: float,
                next_attempt_at: float | None, terminal: bool,
                refund_attempt: bool = False) -> None:
        self._run(_RELEASE_CYPHER, {
            "item_id": item_id,
            "state": (IntakeState.FAILED.value if terminal
                      else IntakeState.QUEUED.value),
            "error": error,
            "next_attempt_at": next_attempt_at,
            "refund": bool(refund_attempt),
        })

    def depth(self) -> int:
        rows = self._run(_DEPTH_CYPHER, {"queued": IntakeState.QUEUED.value})
        return int(rows[0][0]) if rows and rows[0] else 0

    def oldest_queued_at(self) -> float | None:
        rows = self._run(_OLDEST_CYPHER, {"queued": IntakeState.QUEUED.value})
        if not rows or not rows[0] or rows[0][0] is None:
            return None
        return float(rows[0][0])

    def rows(self, *, state: str | None = None,
             limit: int = 100) -> list[CaptureIntakeItem]:
        return [_item_from_row(_row_dict(r))
                for r in self._run(_ROWS_CYPHER,
                                   {"state": state, "limit": int(limit)})]

    def queued_for_session(
            self, session_id: str) -> CaptureIntakeItem | None:
        """The live (not parked) row for one session, oldest first.

        Keyed on `session_id` — the 202's `status_url` addresses the SESSION, and
        a session can be queued many times (a TRUE retry), so the OLDEST live row
        is the one the caller's address is about.
        """
        rows = self._run(_SESSION_CYPHER, {
            "session_id": session_id,
            "failed": IntakeState.FAILED.value,
        })
        if not rows:
            return None
        return _item_from_row(_row_dict(rows[0]))

    def reclaim_stale(self, now: float,
                      after_s: float = CAPTURE_INTAKE_RECLAIM_AFTER_S) -> int:
        """Return items whose worker died to `queued`.

        ⛔ A claim is a LEASE, not a move: without this, a process that dies
        between `claim_next` and `complete` converts an accepted turn into an
        invisible one (`extracting` forever, which no query for `queued` will
        ever see again). The reclaim is bounded by `after_s`, which must exceed
        the longest legitimate capture.
        """
        rows = self._run(_RECLAIM_STALE_CYPHER, {
            "extracting": IntakeState.EXTRACTING.value,
            "queued": IntakeState.QUEUED.value,
            "cutoff": float(now) - float(after_s),
            "error": ("previous drain stopped before this item finished "
                      "(claim reclaimed)"),
        })
        return int(rows[0][0]) if rows and rows[0] else 0


#: `queued_for_session`: what the 202's `status_url` reads. Bounded by the
#: session, not by the queue — `_ROWS_CYPHER` scans the whole queue.
_SESSION_CYPHER = (
    f"MATCH (i:{CAPTURE_INTAKE_LABEL} {{session_id: $session_id}}) "
    "WHERE i.state <> $failed "
    "RETURN i.item_id, i.org_id, i.graph, i.session_id, i.payload, "
    "       i.enqueued_at, i.attempts, i.state, i.last_error "
    "ORDER BY i.item_id ASC LIMIT 1"
)


def _row_dict(row: Any) -> dict:
    """Accept the two shapes FalkorDB drivers return a row in.

    A `RETURN` of N columns arrives as a positional row (list/tuple) from
    `proj.g.query`; a dict row arrives from other drivers and from the memory
    store. Normalising here keeps the column order in ONE place (the Cypher),
    so a column added to a query cannot silently shift a field.
    """
    if isinstance(row, Mapping):
        return dict(row)
    order = ("item_id", "org_id", "graph", "session_id", "payload",
             "enqueued_at", "attempts", "state", "last_error")
    return dict(zip(order, list(row), strict=False))


@dataclass
class IntakeAcceptance:
    """What the door tells the caller about one acceptance."""

    item_id: str
    session_id: str
    enqueued_at: float
    #: `None` when `depth()` could not be read — the acceptance still stands
    #: (the row is what matters); the count is an observability nicety and must
    #: never fail an accept.
    position: int | None = None
    depth: int | None = None
    over_depth: bool = False

    def as_payload(self) -> dict:
        out: dict = {
            "item_id": self.item_id,
            "session_id": self.session_id,
            "accepted_at": self.enqueued_at,
            "status": "queued",
        }
        if self.position is not None:
            out["position"] = self.position
        if self.depth is not None:
            out["depth"] = self.depth
        out["over_depth"] = self.over_depth
        return out


@dataclass
class IntakeCounters:
    """Counters since process start — observability only, never a verdict.

    ⛔ A SEPARATE object from :class:`CaptureIntake` because the queue is built
    per REQUEST (the store is tenant-scoped, so one object cannot serve two
    tenants). Without this seam every caller would mint its own counters and
    every published number would read as zero — the "observability that cannot
    observe" shape.
    """

    accepted: int = 0
    completed: int = 0
    failed: int = 0


@dataclass
class CaptureIntake:
    """The queue's policy layer: accept, claim, settle, publish depth.

    Holds no storage of its own (`store`) and no clock of its own (`now`), so
    every rule below is testable without a graph, a server or a sleep.
    """

    store: IntakeStore
    now: Callable[[], float] = time.time
    max_attempts: int = CAPTURE_INTAKE_MAX_ATTEMPTS
    depth_ceiling: int = CAPTURE_QUEUE_MAX_DEPTH
    counters: IntakeCounters = field(default_factory=IntakeCounters)

    # ── the door ───────────────────────────────────────────────────────────
    def accept(self, *, org_id: str, graph: str | None, session_id: str,
               payload: Mapping[str, Any]) -> IntakeAcceptance:
        """Store the payload and return what to tell the caller.

        ⛔ Raises `IntakeStoreUnavailable` if the row could not be written. The
        caller MUST NOT answer 2xx on that path: the client prunes its spool
        entry the moment it sees a 2xx, so an unwritten row is a destroyed turn
        (#10479). A 503 keeps the turn where it still exists.
        """
        now = float(self.now())
        item = CaptureIntakeItem(
            item_id=item_id_for(now),
            org_id=str(org_id),
            graph=(str(graph) if graph else None),
            session_id=str(session_id),
            payload=dict(payload),
            enqueued_at=now,
            attempts=0,
            state=IntakeState.QUEUED.value,
        )
        # ⛔ DEPTH IS READ BEFORE THE PUT (#14707 review P3): the item itself is
        # not ahead of itself. `position` is the depth the item found when it
        # arrived (D2's "the queue depth ahead of it"); reading after `put()`
        # made every position one too high, and the first item in an empty queue
        # report `position: 1`.
        depth_before = self._safe_depth()
        self.store.put(item)
        self.counters.accepted += 1
        depth_after = None if depth_before is None else depth_before + 1
        return IntakeAcceptance(
            item_id=item.item_id,
            session_id=item.session_id,
            enqueued_at=now,
            position=depth_before,
            depth=depth_after,
            over_depth=(depth_after is not None
                        and depth_after >= self.depth_ceiling),
        )

    # ── the drain ──────────────────────────────────────────────────────────
    def reclaim(self) -> int:
        """Return any dead drain's claimed items to the queue. Idempotent.

        Called at the START of a drain (not on a timer): the only moment a stale
        claim can be acted on is when a drain is about to run anyway.
        """
        try:
            return int(self.store.reclaim_stale(float(self.now())))
        except Exception:  # noqa: BLE001, RUF100 - a reclaim failure must not stop a drain
            return 0

    def next_item(self) -> CaptureIntakeItem | None:
        """Claim the oldest runnable item, respecting per-item backoff."""
        return self.store.claim_next(float(self.now()))

    def finish(self, item: CaptureIntakeItem) -> None:
        """Terminal success: the ONLY deletion path.

        ⛔ Called only after the capture path reported a terminal outcome. An
        item deleted on a merely-attempted pass is the silent loss class.
        """
        self.store.complete(item.item_id)
        self.counters.completed += 1

    def fail(self, item: CaptureIntakeItem, error: str) -> str:
        """Requeue with backoff, or park as `failed` past the attempt bound.

        Returns the state the item now has, so the caller's log line names the
        truth instead of assuming a requeue.
        """
        now = float(self.now())
        terminal = int(item.attempts) >= int(self.max_attempts)
        next_at = None
        if not terminal:
            delay = min(_RETRY_BASE_S * (2 ** max(0, int(item.attempts) - 1)),
                        _RETRY_MAX_S)
            next_at = now + delay
        self.store.release(item.item_id, error=error, now=now,
                           next_attempt_at=next_at, terminal=terminal)
        if terminal:
            self.counters.failed += 1
            return IntakeState.FAILED.value
        return IntakeState.QUEUED.value

    def defer(self, item: CaptureIntakeItem, error: str,
              delay_s: float = _RETRY_BASE_S) -> str:
        """Put an item back WITHOUT charging it an attempt.

        ⛔ The distinction this exists for (#14707 review P1/P2): a TRANSIENT
        condition — capacity, a same-session capture already in flight, a store
        that answered 503 — is not the item's fault, so counting it against
        `CAPTURE_INTAKE_MAX_ATTEMPTS` would convert a busy box into a `failed`
        row, i.e. a lost turn. `fail()` is for the item's own faults and for
        refusals the item can never pass (a 402 the org does not fit, a payload
        over the turn cap); `defer()` for conditions that pass.
        """
        now = float(self.now())
        self.store.release(item.item_id, error=error, now=now,
                           next_attempt_at=now + max(0.0, float(delay_s)),
                           terminal=False, refund_attempt=True)
        return IntakeState.QUEUED.value

    # ── observability ──────────────────────────────────────────────────────
    def park(self, item: CaptureIntakeItem, error: str) -> str:
        """Park an item as `failed` NOW, on a verdict that cannot change.

        ⛔ `fail()` exists for the ATTEMPT BOUND; this exists for a refusal that
        is about the ITEM or the ENTRY (the org does not fit its quota, recording
        is off, the payload is over the turn cap). Retrying those five times over
        four minutes cannot change the answer, and while it waits the row is the
        queue's head. Parking keeps it VISIBLE (`failed`, with the refusal as
        `last_error`) — the design's "exhaustion is not deletion" rule, applied
        one step earlier than the bound.
        """
        self.store.release(item.item_id, error=error, now=float(self.now()),
                           next_attempt_at=None, terminal=True)
        self.counters.failed += 1
        return IntakeState.FAILED.value

    def _safe_depth(self) -> int | None:
        try:
            return int(self.store.depth())
        except Exception:  # noqa: BLE001, RUF100 - a count must never fail an accept
            return None

    def stats(self) -> dict:
        """The published shape (`/health`, and the 202's `intake` block)."""
        c = self.counters
        try:
            depth = int(self.store.depth())
        except IntakeStoreUnavailable:
            return {"available": False, "accepted": c.accepted,
                    "completed": c.completed, "failed": c.failed}
        oldest = None
        try:
            oldest = self.store.oldest_queued_at()
        except IntakeStoreUnavailable:
            oldest = None
        now = float(self.now())
        return {
            "available": True,
            "depth": depth,
            "oldest_age_s": (round(now - oldest, 3)
                             if oldest is not None else None),
            "over_depth": depth >= self.depth_ceiling,
            "depth_ceiling": self.depth_ceiling,
            "accepted": c.accepted,
            "completed": c.completed,
            "failed": c.failed,
        }
