"""Precomputed why-blocks — assembled on the dream cycle, served to the push path.

#8099 (epic #2080 W4 phase 2; the second half of #2081's indicator 2).

WHY IT EXISTS
=============
#2081 shipped the push half of Topology C: ``webhook_delivery.dispatch_pending``
pushes the durable ``:GraphEvent`` rows a subscriber is behind on, and it
satisfies the *no-recompute* half of indicator 2 by construction — the push
path reads the event store and calls no EP function (pinned by
``tests/test_webhook_delivery.py::test_push_path_recomputes_no_beliefs``).
What it does NOT do is push a **why-block**: the envelope carries the raw
durable payload, so a subscriber is told *that* a belief changed and not *why*
it is believed.

The enrichment is deliberately not on the push path. ``why.assemble_why_blocks``
issues a fixed batch of graph reads per call (supports ×2, conflicts,
supersession, tradeoffs, ep). Calling it per push would be a per-push assembly
cost that grows with the batch — exactly the work indicator 2 forbids, and a
cost that would land on a subscriber's latency rather than on background
capacity. So the assembly runs where the belief just changed (**the dream
cycle**), is persisted graph-natively, and the push does ONE bounded read for
the whole page it is about to deliver.

THE THREE CONTRACTS THIS MODULE PINS
====================================
1. **The dream is the only writer.** Nothing on the capture path and nothing on
   the push path writes a snapshot (``precompute*`` is called from the hosted
   dream worker's pool item, inside the per-graph dream lock). That is what
   makes the staleness bound "one dream cadence" a *property of the design*
   rather than a promise: there is no second writer that could drift.
2. **Absent = not computed, and absent costs a push nothing.** A graph with no
   subscriptions gets ZERO writes from this module — the subscription probe
   runs first and a subscriber-less graph takes the return before any index or
   node is created (the same discipline ``dispatch_pending`` follows, and the
   same reason: a maintenance pass that changed nothing must leave the graph
   byte-identical). A push whose points have no snapshot simply omits the
   ``why`` key; the envelope's existing fields stay byte-identical.
3. **Fail-open, both directions.** An assembly error never fails a dream and
   never fails a delivery. A snapshot read error degrades the push to "no why"
   — never to a failed delivery, never to a re-delivery.

WHAT A SNAPSHOT IS (and is not)
===============================
``:WhySnapshot {point_id, block, assembled_at, confidence_mean}`` — one node per
Point, holding the canonical §3.1.4 why-block (``why.assemble_why_blocks``'
output) as JSON. It is a DERIVED artifact:

* a ``rebuild_all`` from the JSONL journal DROPS these nodes (they carry no
  journal record), which is correct — the next dream recreates them;
* it is invisible to the ``consistency`` digest/parity comparison, which walks
  ``_GRAPH_VIEW_LABELS`` only (Point + the reference-fold entity labels);
* it is NOT the Postgres-sourced cache of ``cache_projection`` (interface B —
  "the cache is a destination, never an input"). This is a graph-local
  precompute of a graph-local read, and nothing reads it to decide what is
  true; only what to *serve*.

The wire shape is ``volunteer.why_entry`` — the SAME projection ``POST
/v1/context`` and ``volunteer_context()`` use for their ``why`` list, so the
pushed entry and the pulled entry for one point cannot drift (indicator 1).
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from .env_truthy import env_flag
from .why import assemble_why_blocks, point_ids_in_payload

logger = logging.getLogger("tortoise.why_snapshot")

#: Kill switch (mirrors ``webhook_delivery.KILL_SWITCH_ENV``). Falsy → the
#: dream pass writes nothing and a push carries no ``why``; the delivery layer
#: itself keeps working. Unset/blank/garbage → ON (``env_flag``'s contract: an
#: unknown value must not silently disable a feature an operator never touched).
SNAPSHOT_ENV = "TORTOISE_WHY_SNAPSHOT"

#: Points assembled per dream pass. The pass's own ``affected_claims`` is
#: budget-bounded (op_cap 200); this cap additionally bounds the pending-event
#: half so one pathological backlog cannot turn a background pass unbounded.
MAX_POINTS_PER_PASS = 400

#: Events inspected for the "what will be pushed next" half of the point set.
MAX_PENDING_EVENTS = 200

#: Point ids read per committed page on the push path. A page is at most
#: ``webhook_delivery.DEFAULT_BATCH`` events, each touching a handful of ids;
#: the cap is the defence against a batch of operator events fanning out.
MAX_WHY_IDS_PER_PAGE = 200

#: Snapshots older than this are reaped on a later pass. Not a correctness
#: bound (a stale snapshot is overwritten by the next dream that assembles its
#: point) — a hygiene bound, so a graph whose points churn does not accumulate
#: orphans.
RETENTION_DAYS = 14

_SCHEMA_ATTR = "_tortoise_why_snapshot_schema"


def enabled() -> bool:
    """Is the dream-cycle precompute on? (kill switch; default ON)."""
    return env_flag(SNAPSHOT_ENV, True)


def _iso_now(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat()


def ensure_schema(proj) -> None:
    """Create the ``:WhySnapshot`` exact-match index (idempotent, non-fatal).

    Called only when a write is actually about to happen — index creation is a
    graph WRITE, and a pass with nothing to store must leave the graph alone.
    """
    if getattr(proj, _SCHEMA_ATTR, False):
        return
    try:
        proj.g.query("CREATE INDEX FOR (n:WhySnapshot) ON (n.point_id)")
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("why_snapshot: point_id index may already exist")
    setattr(proj, _SCHEMA_ATTR, True)


# ── Read half (the push path) ─────────────────────────────────────────────

def read_blocks(proj, point_ids: list[str]) -> dict[str, dict]:
    """Return ``{point_id: canonical_block}`` for the points that HAVE a snapshot.

    ONE single-node read on the ``point_id`` index — no traversal, no EP call,
    no assembly. A point with no snapshot is simply absent from the result
    ("absent = not computed", contract 2 of the module docstring).

    Fail-open: any graph error returns ``{}`` (the caller degrades to "no why"
    rather than failing the delivery, contract 3).
    """
    ids = [pid for pid in (point_ids or []) if pid][:MAX_WHY_IDS_PER_PAGE]
    if not ids:
        return {}
    try:
        rows = proj.g.query(
            "MATCH (w:WhySnapshot) WHERE w.point_id IN $ids "
            "RETURN w.point_id, w.block, w.assembled_at",
            params={"ids": ids},
        ).result_set
    except Exception as e:  # noqa: BLE001, RUF100 — read fail-open (contract 3)
        logger.warning("why_snapshot: snapshot read failed: %s", e)
        return {}
    out: dict[str, dict] = {}
    newest: dict[str, str] = {}
    for pid, blob, assembled_at in rows or []:
        if not isinstance(pid, str) or not pid:
            continue
        # MERGE keys on point_id, so a duplicate node can only come from a
        # concurrent writer the dream lock excludes — resolve it deterministically
        # (newest wins) rather than letting the store's row order decide.
        stamp = assembled_at if isinstance(assembled_at, str) else ""
        if pid in out and newest.get(pid, "") >= stamp:
            continue
        try:
            block = json.loads(blob) if isinstance(blob, str) else None
        except (TypeError, ValueError):
            block = None
        if not isinstance(block, dict) or block.get("point_id") != pid:
            # A corrupt/unstamped snapshot is a MISS, never a fabricated block.
            continue
        out[pid] = block
        newest[pid] = stamp
    return out


def read_why_entries(proj, point_ids: list[str]) -> dict[str, dict]:
    """``{point_id: §3.2.2 wire why-entry}`` — the push envelope's ``why`` items.

    Projects through ``volunteer.why_entry``, the ONE wire projection
    ``/v1/context`` uses, so the two surfaces cannot disagree about the shape
    of a why-entry (indicator 1). Pure dict work: no extra graph reads.
    """
    blocks = read_blocks(proj, point_ids)
    if not blocks:
        return {}
    from .volunteer import why_entry  # local import: avoid a module cycle

    out: dict[str, dict] = {}
    for pid, block in blocks.items():
        try:
            out[pid] = why_entry(block)
        except Exception as e:  # noqa: BLE001, RUF100 — one bad block ≠ no delivery
            logger.warning("why_snapshot: entry projection failed for %s: %s",
                           pid, e)
    return out


# ── Write half (the dream cycle) ──────────────────────────────────────────

def _drop(proj, point_ids: list[str]) -> int:
    """Delete the snapshots of points that no longer exist. Returns the count."""
    ids = [pid for pid in point_ids if pid]
    if not ids:
        return 0
    try:
        rows = proj.g.query(
            "MATCH (w:WhySnapshot) WHERE w.point_id IN $ids "
            "DELETE w RETURN count(*)",
            params={"ids": ids},
        ).result_set
    except Exception as e:  # noqa: BLE001, RUF100 — hygiene, never a pass failure
        logger.warning("why_snapshot: snapshot drop failed: %s", e)
        return 0
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _vanished(proj, point_ids: list[str]) -> list[str]:
    """The requested ids that are NOT ``:Point`` nodes (deleted, or never existed).

    An EXISTENCE read, deliberately independent of the assembly: the assembly is
    fail-open and returns ``{}`` both when it failed and when nothing it was
    asked about exists, so inferring "gone" from a missing block would delete a
    good snapshot set on a transient read error. A failed existence read returns
    ``[]`` (nothing is dropped) for the same reason.
    """
    ids = [pid for pid in point_ids if pid]
    if not ids:
        return []
    try:
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id",
            params={"ids": ids},
        ).result_set
    except Exception as e:  # noqa: BLE001, RUF100 — never delete on a read error
        logger.warning("why_snapshot: existence read failed: %s", e)
        return []
    alive = {row[0] for row in rows or [] if row and isinstance(row[0], str)}
    return [pid for pid in ids if pid not in alive]


def purge_stale(proj, *, now: datetime | None = None,
                retention_days: int = RETENTION_DAYS) -> int:
    """Delete snapshots older than ``retention_days``. Idempotent; bounded."""
    cutoff = ((now or datetime.now(UTC))
              - timedelta(days=int(retention_days))).isoformat()
    try:
        rows = proj.g.query(
            "MATCH (w:WhySnapshot) WHERE w.assembled_at < $cutoff "
            "WITH w LIMIT 5000 DELETE w RETURN count(*)",
            params={"cutoff": cutoff},
        ).result_set
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("why_snapshot: stale purge failed: %s", e)
        return 0
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def precompute(proj, point_ids: list[str], *, now: datetime | None = None,
               purge: bool = True) -> dict:
    """Assemble + persist why-blocks for ``point_ids``. NEVER raises.

    ``{"enabled", "points", "written", "dropped", "purged", "reason"}``

    The assembly is ``why.assemble_why_blocks`` — the SAME bounded, zero-LLM
    assembly ``/v1/context`` runs, one batch for the whole id set (never a
    per-point loop), so a snapshot's block is byte-equal to what the pull
    surface would compute at this instant.

    A requested id with NO assembled block is either not a Point (never got a
    block by design — the ep read is the existence anchor) or has been deleted,
    so its snapshot is dropped: serving a why-block for a point whose
    ``PointRetracted`` event is the one being delivered would state the
    opposite of the event. The drop is driven by an EXISTENCE read
    (:func:`_vanished`), never by a missing block, because a failed assembly is
    also blockless and a transient read error must not delete a good snapshot
    set — ``purge``'s retention, not a live error, is what reaps snapshots.
    """
    summary = {"enabled": enabled(), "points": 0, "written": 0, "dropped": 0,
               "purged": 0, "reason": None}
    if not summary["enabled"]:
        summary["reason"] = "disabled"
        return summary
    ids: list[str] = []
    for pid in point_ids or []:
        if isinstance(pid, str) and pid and pid not in ids:
            ids.append(pid)
    ids = ids[:MAX_POINTS_PER_PASS]
    summary["points"] = len(ids)
    if not ids:
        summary["reason"] = "no_points"
        return summary
    blocks = assemble_why_blocks(proj, ids)
    written_rows: list[dict] = []
    stamp = _iso_now(now)
    for pid in ids:
        block = blocks.get(pid)
        if not isinstance(block, dict):
            continue
        ep = block.get("ep") if isinstance(block.get("ep"), dict) else {}
        try:
            mean = float(ep.get("confidence_mean") or 0.0)
        except (TypeError, ValueError):
            mean = 0.0
        written_rows.append({
            "point_id": pid,
            "block": json.dumps(block, ensure_ascii=False, sort_keys=True,
                                default=str),
            "ts": stamp,
            "mean": mean,
        })
    if written_rows:
        ensure_schema(proj)
        try:
            proj.g.query(
                "UNWIND $rows AS row "
                "MERGE (w:WhySnapshot {point_id: row.point_id}) "
                "SET w.block = row.block, w.assembled_at = row.ts, "
                "    w.confidence_mean = row.mean",
                params={"rows": written_rows},
            )
            summary["written"] = len(written_rows)
        except Exception as e:  # noqa: BLE001, RUF100 — never fail the dream
            logger.warning("why_snapshot: snapshot write failed: %s", e)
            summary["reason"] = "write_failed"
            return summary
    # A snapshot whose point is GONE is dropped (a retraction's push must not
    # carry a block claiming the point is live). Driven by an existence read,
    # never by a missing block — see `_vanished`.
    gone = _vanished(proj, ids)
    if gone:
        summary["dropped"] = _drop(proj, gone)
    if purge:
        summary["purged"] = purge_stale(proj, now=now)
    return summary


def pending_point_ids(proj, *, min_cursor: int = 0,
                      limit: int = MAX_PENDING_EVENTS) -> list[str]:
    """Point ids referenced by events subscribers are still behind on.

    The dream's ``affected_claims`` is what the EP pass MOVED; this is what will
    actually be PUSHED next. The union is the point set worth assembling: a
    ``PointAdded`` whose belief never moved still deserves a why-block on its
    event, and the EP window may legitimately not include it.

    Fail-open: a read error returns ``[]`` (the pass still assembles the
    affected claims).
    """
    from .event_store import read_after

    try:
        events = read_after(proj, int(min_cursor), limit=max(1, int(limit)))
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("why_snapshot: pending event read failed: %s", e)
        return []
    ids: list[str] = []
    for event in events:
        for pid in point_ids_in_payload(event.get("payload")):
            if pid not in ids:
                ids.append(pid)
    return ids


def precompute_for_dream(proj, dream_result: dict | None = None, *,
                         now: datetime | None = None) -> dict:
    """The dream-cycle entry point: precompute for the points this pass matters to.

    Runs INSIDE the per-graph dream lock (the dream pool item), after the EP
    pass — so every snapshot carries post-dream numbers (indicator 3), and the
    push that follows the pass cannot serve a pre-dream belief.

    Subscriber gate FIRST (contract 2): a graph with no live subscription takes
    the ``no_subscribers`` return before any index, node or query beyond the one
    probe. This is why the feature costs a tenant who never subscribed nothing.
    """
    summary = {"enabled": enabled(), "points": 0, "written": 0, "dropped": 0,
               "purged": 0, "reason": None}
    if not summary["enabled"]:
        summary["reason"] = "disabled"
        return summary
    try:
        rows = proj.g.query(
            "MATCH (s:ContextSubscription) WHERE s.disabled_at IS NULL "
            "RETURN count(s), min(coalesce(s.cursor, 0))",
        ).result_set
    except Exception as e:  # noqa: BLE001, RUF100 — never fail the dream
        logger.warning("why_snapshot: subscription probe failed: %s", e)
        summary["reason"] = "subscription_probe_failed"
        return summary
    n_subs = int(rows[0][0] or 0) if rows else 0
    if n_subs == 0:
        summary["reason"] = "no_subscribers"
        return summary
    min_cursor = int(rows[0][1] or 0) if rows else 0
    ids = [pid for pid in ((dream_result or {}).get("affected_claims") or [])
           if isinstance(pid, str)]
    ids.extend(pending_point_ids(proj, min_cursor=min_cursor))
    return precompute(proj, ids, now=now)
