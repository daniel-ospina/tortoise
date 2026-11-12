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
1. **The dream is the only writer, and it is EVERY dream pass.** Nothing on the
   capture path and nothing on the push path writes a snapshot
   (``precompute*`` is called inside the per-graph dream lock, after the pass —
   from the write-triggered drain AND from ``POST /v1/dream``'s three branches,
   which is the pass the scheduler actually runs). That is what makes the
   staleness bound "one dream cadence" a *property of the design* rather than a
   promise: there is no second writer that could drift, and no belief-moving
   dream that skips the refresh (#8099 review F2). ⚠️ One pass is NOT covered
   and is stated rather than implied: the capture path's own ingest EP pass
   (``hosted_api._apply_capture_ingest_ep``) moves beliefs outside the dream
   worker; its snapshots go stale until the next dream, and this is the same
   residual that pass already carries for the event store (#3086).
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
#: orphans. Enforced only by an ENABLED pass — see :func:`enabled`.
RETENTION_DAYS = 14

_SCHEMA_ATTR = "_tortoise_why_snapshot_schema"

#: §17.1: the lane this module's writes declare. A `:WhySnapshot` is a DERIVED
#: artifact — a rebuild drops it and the next dream recreates it — so it is an
#: UNJOURNALLED writer that must be DECLARED rather than left to the census.
#: Read back at the write handle by the pin
#: (`tests/test_why_snapshot_8099.py::test_the_snapshot_writes_declare_their_lane`),
#: never rendered into a refusal.
WHY_SNAPSHOT_WRITE_LANE = "why.snapshot-cache"


def _run(proj, cypher: str, params: dict | None = None):
    """This module's ONE gateway to the projection's write handle.

    A MUTATING statement is DECLARED — `_cache_write_authorized(
    WHY_SNAPSHOT_WRITE_LANE)` — so #9124 closing the third door (#9079) refuses
    an undeclared second writer without refusing this product surface. A
    statement the classifier can PROVE read-only runs unscoped (there is nothing
    for the guard to refuse), which is why the read half shares this gateway.

    The import is function-local for the same reason
    `webhook_delivery._run`'s is: this module is imported by `hosted_api`, which
    imports the projection, so a module-level import here would make the
    dependency direction circular.

    ⛔ A REFUSAL IS NEVER SWALLOWED. Every handler on this path that exists for
    an ORDINARY failure re-raises `DirectCacheWriteRefused` ahead of its broad
    clause — `ensure_schema` (the index DDL's "already exists"), `_drop`, the
    purge, and `precompute`'s write arm. A refusal means the statement never
    reached the store, which is categorically NOT the transient graph hiccup
    those handlers absorb: treating it as one is the silent-loss shape §17.1
    exists to prevent, and for this module it would leave the push serving
    whichever blocks happened to land (#8099 review F1, the #9233 rule).
    """
    from tortoise.projection import _cache_write_authorized, _statement_writes

    if _statement_writes(cypher):
        with _cache_write_authorized(WHY_SNAPSHOT_WRITE_LANE):
            return proj.g.query(cypher, params=params)
    return proj.g.query(cypher, params=params)


def enabled() -> bool:
    """Is the dream-cycle precompute on? (kill switch; default ON).

    ⚠️ THE SWITCH GATES BOTH HALVES — the WRITE (`precompute`,
    `precompute_for_dream`) and the READ (`read_blocks`, and so
    `read_why_entries` and every `why` the push carries). It has to: the
    operator's only lever when the assembly is producing wrong blocks is to
    stop serving them, and a switch that only stops the REFRESH keeps serving
    exactly the blocks the operator mistrusts, at whatever age they reached
    before the switch was thrown (#8099 review F2).

    What the switch does NOT do is run hygiene: a disabled pass writes nothing
    at all — no snapshot, no `_drop`, no `purge_stale` — so the retention bound
    below is enforced from the first ENABLED pass after the switch returns, not
    while the feature is off. That is deliberate (a disabled feature must leave
    the graph byte-identical) and it is inert, because a snapshot the read path
    refuses to serve cannot be seen by anyone.
    """
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
    from tortoise.projection import DirectCacheWriteRefused

    try:
        _run(proj, "CREATE INDEX FOR (n:WhySnapshot) ON (n.point_id)")
    except DirectCacheWriteRefused:
        # NOT an "already exists": the statement never reached the store, so
        # the cache flag must stay unset and the refusal must reach the caller.
        raise
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("why_snapshot: point_id index may already exist")
    setattr(proj, _SCHEMA_ATTR, True)


# ── Read half (the push path) ─────────────────────────────────────────────

def read_blocks(proj, point_ids: list[str]) -> dict[str, dict]:
    """Return ``{point_id: canonical_block}`` for the points that HAVE a snapshot.

    ONE bounded read on the ``point_id`` index — no traversal, no EP call, no
    assembly. A point with no snapshot is simply absent from the result
    ("absent = not computed", contract 2 of the module docstring).

    ⛔ THE READ IS VALIDITY-FILTERED, and that is the RETRACTION guarantee
    (#8099 review F1). The statement joins the ``:Point`` node and excludes a
    TERMINAL one (``live._terminal_excluded``, the one spelling of the
    predicate), so a snapshot whose point was deleted, retracted, invalidated or
    superseded is a MISS at read time. Dropping it in the writer's pass is NOT
    enough for the guarantee the module docstring states: `dispatch_pending` is
    also driven by the scheduled retry sweep and on demand
    (`webhook_delivery.py:1221-1226`), neither of which precomputes, and even
    the dream pass only sees a BOUNDED id set (``MAX_PENDING_EVENTS`` /
    ``MAX_POINTS_PER_PASS``) — so a retraction outside that window would ride
    out with a block claiming the point is live. The field's answer to a cache
    whose source mutates behind it is read-time validity filtering (Zep /
    Graphiti filter bi-temporal edges on every read rather than trusting the
    invalidation pass to have run first), and here it costs one extra pattern
    match on an already-bounded id list. ``precompute``'s ``_vanished`` drop
    stays as what it is: cache HYGIENE (bounded growth), not the guarantee.

    Fail-open: any graph error returns ``{}`` (the caller degrades to "no why"
    rather than failing the delivery, contract 3).

    ⚠️ The KILL SWITCH gates this read (#8099 review F2): disabled means a push
    carries no ``why``, which is what :data:`SNAPSHOT_ENV` says it does. Serving
    a snapshot written before the switch was thrown is exactly what an operator
    throwing it is trying to stop.
    """
    if not enabled():
        return {}
    ids = [pid for pid in (point_ids or []) if pid][:MAX_WHY_IDS_PER_PAGE]
    if not ids:
        return {}
    from .live import _terminal_excluded

    try:
        rows = _run(
            proj,
            "MATCH (w:WhySnapshot) WHERE w.point_id IN $ids "
            "MATCH (n:Point {id: w.point_id}) "
            f"WHERE {_terminal_excluded('n.status')} "
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
    from tortoise.projection import DirectCacheWriteRefused

    try:
        rows = _run(
            proj,
            "MATCH (w:WhySnapshot) WHERE w.point_id IN $ids "
            "DELETE w RETURN count(*)",
            params={"ids": ids},
        ).result_set
    except DirectCacheWriteRefused:
        raise                     # the delete never landed — not "dropped 0"
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
        rows = _run(
            proj,
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
    from tortoise.projection import DirectCacheWriteRefused

    try:
        rows = _run(
            proj,
            "MATCH (w:WhySnapshot) WHERE w.assembled_at < $cutoff "
            "WITH w LIMIT 5000 DELETE w RETURN count(*)",
            params={"cutoff": cutoff},
        ).result_set
    except DirectCacheWriteRefused:
        raise                     # the purge never landed — not "purged 0"
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

    ⛔ #11928: ``assemble_why_blocks`` returns a block for every epistemic label
    now, but this cache serves ``:Point`` snapshots only (its read half and
    ``_vanished`` are Point-anchored), so the id set is intersected with the
    existence read BEFORE the assembly. Do not "fix" that filter by widening
    the assembly alone — that produces a snapshot the push path can never serve.

    ⛔ It raises ONLY for a §17.1 REFUSAL of its own write
    (``DirectCacheWriteRefused``): that is not a transient graph error, it means
    the statement never landed, and folding it into ``reason="write_failed"``
    would let the push serve whichever blocks happened to land while reporting a
    best-effort miss (#8099 review F1; the #9233 rule). Every ordinary failure
    stays fail-open.
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
    # `assemble_why_blocks` is internally fail-open (a graph error returns {}),
    # but this function's own contract is "NEVER raises", so a raise from the
    # assembly is absorbed HERE too — and, critically, an absorbed failure is
    # NOT a reason to drop anything: the existence read below decides that,
    # precisely because "no block" and "the assembly broke" look identical
    # (#8099 review F4).
    #
    # ⛔ #11928 — THE ID SET IS INTERSECTED WITH WHAT THIS CACHE CAN SERVE, and
    # the intersection is the SAME existence read that decides the drop, so the
    # pass pays for it once. `assemble_why_blocks` now returns a block for every
    # epistemic label (a goal ``:Object`` is explainable), but this cache's READ
    # half (``read_blocks``, and ``_vanished`` itself) is still ``:Point``-anchored
    # — widening it is a separate decision with its own cost (a per-label sweep
    # on the PUSH path, whose whole point is one bounded read). Writing a
    # snapshot the read half will never serve AND this function would then
    # delete again — on every pass, for every non-Point id the EP pass moved —
    # is churn with no reader at the end of it. So a non-Point id is dropped
    # BEFORE the assembly rather than written and reaped.
    #
    # ⚠️ THE READ-ERROR ARM IS NOT A NO-OP, and saying so is the point (#11928
    # review 2): ``_vanished`` returns ``[]`` on a read error ("nothing provably
    # gone"), which keeps the id set intact — so on THAT pass a non-Point id can
    # be assembled and written, and the next successful pass reaps it. That is a
    # bounded, transient write-then-reap window (the snapshot is never SERVED:
    # ``read_blocks`` joins ``:Point``), not a wrong answer, and it is what the
    # alternative would trade for — skipping the whole pass on a transient read
    # error, i.e. serving no why-block to any subscriber for a graph whose
    # existence read hiccuped.
    gone = _vanished(proj, ids)
    if gone:
        _gone_set = set(gone)
        ids = [pid for pid in ids if pid not in _gone_set]
    try:
        blocks = assemble_why_blocks(proj, ids)
    except Exception as e:  # noqa: BLE001, RUF100 — contract: never raises
        logger.warning("why_snapshot: assembly failed: %s", e)
        summary["reason"] = "assembly_failed"
        return summary
    written_rows: list[dict] = []
    stamp = _iso_now(now)
    for pid in ids:
        block = blocks.get(pid)
        if not isinstance(block, dict):
            continue
        ep = block.get("ep") if isinstance(block.get("ep"), dict) else {}
        # #11702: an ABSENT belief is `None`, not 0.0. Coercing it to 0.0 put
        # "maximum disbelief" into a column named ``mean`` for a claim nothing
        # had been measured on; the snapshot node then carried a number no
        # reader could distinguish from a real posterior of 0.0. A None here
        # removes the property on the SET below, which is the honest encoding
        # of "there is no belief".
        try:
            raw_mean = ep.get("confidence_mean")
            mean = float(raw_mean) if raw_mean is not None else None
        except (TypeError, ValueError):
            mean = None
        written_rows.append({
            "point_id": pid,
            "block": json.dumps(block, ensure_ascii=False, sort_keys=True,
                                default=str),
            "ts": stamp,
            "mean": mean,
        })
    if written_rows:
        ensure_schema(proj)
        from tortoise.projection import DirectCacheWriteRefused

        try:
            _run(
                proj,
                "UNWIND $rows AS row "
                "MERGE (w:WhySnapshot {point_id: row.point_id}) "
                "SET w.block = row.block, w.assembled_at = row.ts, "
                "    w.confidence_mean = row.mean",
                params={"rows": written_rows},
            )
            summary["written"] = len(written_rows)
        except DirectCacheWriteRefused:
            raise             # declared-and-refused ≠ "write_failed", see docstring
        except Exception as e:  # noqa: BLE001, RUF100 — never fail the dream
            logger.warning("why_snapshot: snapshot write failed: %s", e)
            summary["reason"] = "write_failed"
            return summary
    # A snapshot whose point is GONE is dropped — CACHE HYGIENE, not the
    # guarantee (#8099 review F1). The guarantee is `read_blocks`' validity
    # join: the push path refuses a block whose point is deleted or terminal
    # whether or not this drop ever ran, because `dispatch_pending` also runs
    # from the retry sweep and on demand (neither precomputes) and this pass
    # only ever sees a bounded id set. Driven by an existence read, never by a
    # missing block — see `_vanished`. #11928: the read was already taken above
    # (it also gates the id set), so it is not repeated here.
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
