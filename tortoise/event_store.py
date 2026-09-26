"""Durable per-tenant event store — `:GraphEvent` nodes in the org's graph.

#432 (subscriptions + claim lifecycle): the SDK emit hook writes graph-change
events here. The graph namespace IS the org partition — there is NO `org_id`
property (plan-review P2): the SDK writes into its own graph, and REST/MCP
isolation comes from the namespace (server-derived, never client-supplied).

Schema (idempotent; mirror sdk.py `_ensure_registry_indexes` pattern):
- exact-match index on `event_id` FIRST, then a unique constraint
  (FalkorDB requires the index before the constraint)
- plain index on `seq` (per-graph = per-org cursor reads)

Delivery: at-least-once. `append_event` catches a unique-constraint violation
on `event_id` (duplicate append) → logs and skips (never crashes the
mutation) — `event_id` is a server-side ULID, so a collision is a retry
artifact, never legitimate data. `read_after` additionally dedups (defense in
depth).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_SCHEMA_ATTR = "_tortoise_event_schema"

# The largest ``last_seq`` the store will carry. Two hard ceilings apply, and
# the lower one wins:
#
# * DOUBLE PRECISION (the binding one). `ensure_event_schema` puts a RANGE
#   index on `:GraphEvent.seq`, and FalkorDB compares numeric ranges in double
#   precision, where integers above 2**53 are no longer all representable — so
#   `read_after`'s `WHERE e.seq > $after` SILENTLY DROPS rows in that range.
#   Verified on FalkorDB 4.20.4: with the index, `e.seq > 9007199254740992`
#   returns the row at 2**53+2 but NOT the one at 2**53+1 (2**53+1 rounds to
#   2**53 in a double, so it fails `> 2**53`); without the index both are
#   returned. A counter in that range therefore re-creates the exact #4653 harm
#   this guard exists to prevent — events that exist and are never delivered —
#   with no 410 to tell the subscriber. `MAX_SEQ` is the last counter value for
#   which the immediately following `seq` is still compared exactly.
# * INT64 (the wrap ceiling). ``next_seq`` is `m.last_seq + 1` in 64-bit
#   arithmetic, so a counter at INT64_MAX WRAPS to INT64_MIN on the next bump —
#   there is no saturating step (reproduced on FalkorDB 4.20.4).
#
# ``first_seq`` is NOT bumped and is compared app-side in `events_poll` (never
# through the index), so it may legitimately sit ABOVE ``MAX_SEQ`` — e.g.
# ``MAX_SEQ + 1``, the floor of a log whose last write was ``MAX_SEQ`` — and is
# bounded only by ``MAX_INT64``.
MAX_INT64 = 2 ** 63 - 1
MAX_SEQ = 2 ** 53 - 1


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def ensure_event_schema(proj) -> None:
    """Create index + unique constraint for :GraphEvent (idempotent, non-fatal).

    Runs once per projection (cached on the projection object). Constraint
    creation is wrapped — FalkorDB #664 crash reports on string-property
    unique constraints; the defensive try/except + verification pattern keeps
    schema creation non-fatal.
    """
    if getattr(proj, _SCHEMA_ATTR, False):
        return
    g = proj.g
    try:
        # Exact-match index first — the constraint requires it (Pattern Research).
        g.query("CREATE INDEX FOR (n:GraphEvent) ON (n.event_id)")
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("event_store: event_id index may already exist")
    try:
        g.query(
            "GRAPH.CONSTRAINT CREATE tortoise_ge_uid UNIQUE NODE GraphEvent "
            "PROPERTIES 1 event_id"
        )
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("event_store: unique constraint may already exist")
    try:
        g.query("CREATE INDEX FOR (n:GraphEvent) ON (n.seq)")
    except Exception:  # noqa: BLE001, RUF100
        logger.debug("event_store: seq index may already exist")
    setattr(proj, _SCHEMA_ATTR, True)


def next_seq(proj) -> int:
    """Return the next monotonic per-graph event seq (atomic in-graph counter).

    One `:GraphEventMeta` node per graph holds last_seq; MERGE ON CREATE/MATCH
    bumps it in a single GRAPH.QUERY. (Atomicity against the deployed FalkorDB
    version is tracked in the plan Open Items; the concurrency test pins it.)
    """
    rows = proj.g.query(
        "MERGE (m:GraphEventMeta) "
        "ON CREATE SET m.last_seq = 1, m.first_seq = 1 "
        "ON MATCH SET m.last_seq = m.last_seq + 1 "
        "RETURN m.last_seq"
    ).result_set
    return int(rows[0][0])


def capture_watermark(proj) -> dict | None:
    """Read the per-graph ``:GraphEventMeta`` HIGH-WATER MARK, or ``None``.

    #4653: returned as a sidecar-shaped entry (``{"last_seq": int}``) so a
    pre-wipe capture can be carried through a wipe+replay. ``None`` — never
    ``{"last_seq": 0}`` — when no counter node exists: a graph that has never
    emitted an event must come back with no counter, so the next
    ``next_seq`` creates it at 1 exactly as it always did. Inventing a
    counter here would make "never emitted" indistinguishable from "emitted
    an event with seq 0", and ``events_poll``'s ``after_seq == 0`` sentinel
    already owns the latter meaning.

    ``last_seq`` is the part of the watermark that has NO derivable
    substitute, which is why a rebuild has to carry it rather than recompute
    it: the node is the sole record of the highest ``seq`` ever handed out, and
    the JSONL journal carries no ``seq`` at all — ``_emit_event`` even writes a
    ``:GraphEvent`` with no JSONL record for payload-only emits (``sdk.py``
    ``_emit_event("PointRetracted", {"id": ...})``). ``first_seq`` is not
    carried either (its pre-wipe purge floor is lost too), but a truthful
    substitute — ``last_seq + 1`` — is derivable from the carried counter, so
    nothing is lost by leaving it behind.

    A live value outside ``0 .. MAX_SEQ`` RAISES rather than being captured.
    Such a counter is already unusable: above ``MAX_SEQ`` its events are not
    reliably delivered (§ ``MAX_SEQ``), a negative one can never satisfy
    ``read_after``'s ``seq > cursor``, and capturing it would also write a
    rescue file this build's own loader REFUSES — the writer-⊆-loader invariant
    the sidecar depends on. Raising here aborts the rebuild BEFORE the wipe:
    through ``rebuild_all``'s ``capture_failed`` gate, and by propagating
    straight out of the journal-only ``rebuild()``, which has no such gate
    (#2943).
    """
    rows = proj.g.query(
        "MATCH (m:GraphEventMeta) RETURN max(m.last_seq)").result_set
    if not rows or rows[0][0] is None:
        return None
    value = int(rows[0][0])
    if not 0 <= value <= MAX_SEQ:
        raise ValueError(
            f"the live :GraphEventMeta.last_seq is {value}, outside the "
            f"event-store integer domain (0 <= last_seq <= {MAX_SEQ}) — above "
            f"it the seq range index no longer compares exactly, so events "
            f"are silently undeliverable; below it every seq sits under every "
            f"cursor. The counter must be repaired before rebuilding")
    return {"last_seq": value}


def reestablish_watermark(proj, carried_last_seq: int | None) -> int | None:
    """Re-establish ``:GraphEventMeta`` after a wipe+replay (#4653).

    The ``last_seq`` monotonicity guard is ``hosted_backup._restore_event_meta``'s
    (#3902): ``last_seq = max(carried, max(:GraphEvent.seq))`` — whatever the
    carried value is, the ordering key must never sit below the top seq the
    replayed log already uses, because that equality IS the collision this
    exists to prevent. The ``first_seq`` rule deliberately DIFFERS from that
    path: the backup/restore sibling carries ``first_seq`` out of the dump
    (there the ``:GraphEvent`` rows are copied, so the purge floor survives),
    while this counterpart always re-derives it — ``min(:GraphEvent.seq)``, or
    ``last_seq + 1`` when the log is empty, which is the ``_refresh_first_seq``
    contract (every cursor below the next write is expired). After a rebuild the
    ``:GraphEvent`` rows are NOT replayed today (#4664), so this is normally
    ``last_seq + 1``: the truthful statement that the stream was truncated,
    which lets ``events_poll`` answer 410 instead of silently returning ``[]``
    forever to a subscriber parked above the fresh counter. Note that
    ``first_seq`` is written only when it would be RAISED — i.e. when this
    write's floor is above the one already stored. It is not lowered, because a
    counter created by ``next_seq`` can still hold ``first_seq = 1`` while
    carrying a high ``last_seq``, and lowering the floor would leave a
    subscriber whose cursor sits under it reading ``[]`` forever instead of
    getting the truthful 410.

    ``carried_last_seq is None``, no replayed event **and** no live counter →
    the node is left ABSENT and ``None`` is returned, mirroring the restore
    path's "old dump, no events, no counter — nothing to seed". A graph that
    never emitted does not gain a counter from a rebuild.

    A value outside ``0 .. MAX_SEQ`` is REFUSED (``ValueError``) rather than
    written, whichever input produced it — the carry, a replayed
    ``:GraphEvent.seq``, or a live counter this monotone write preserved. The
    pre-wipe validator already refuses such a sidecar at the untrusted boundary
    and ``capture_watermark`` refuses to carry one; this is the sink-side half of
    the same guard.

    The caller owns error handling: this runs after the wipe, so a raise here
    would leave the store without its watermark — ``rebuild_all``/``rebuild``
    catch and log the consequence rather than propagate it (#2943 "no loss
    without proof").
    """
    rows = proj.g.query(
        "MATCH (e:GraphEvent) RETURN max(e.seq), min(e.seq)").result_set
    max_seq, min_seq = (rows[0] if rows else (None, None))
    max_seq = int(max_seq) if max_seq is not None else None
    min_seq = int(min_seq) if min_seq is not None else None
    last_seq = int(carried_last_seq) if carried_last_seq is not None else None
    # A live counter is folded in BEFORE the arithmetic, not left to the
    # monotone write below: `first_seq` is derived from the FINAL `last_seq`
    # (the maximum of the carry, the replayed log and the live counter), and a
    # counter left by a previous run or bumped by a concurrent emitter would
    # otherwise keep a floor below its own value — a subscriber parked between
    # the two would read `[]` forever instead of getting the truthful 410.
    live_rows = proj.g.query(
        "MATCH (m:GraphEventMeta) RETURN max(m.last_seq)").result_set
    live = live_rows[0][0] if live_rows else None
    live = int(live) if live is not None else None
    if live is not None:
        last_seq = live if last_seq is None else max(last_seq, live)
    if max_seq is not None:
        last_seq = max_seq if last_seq is None else max(last_seq, max_seq)
    if last_seq is None:
        return None
    # The bound is applied to the FINAL value, after the fold and the
    # monotonicity guard: checking the carry alone would let an out-of-domain
    # REPLAYED `:GraphEvent.seq` — or a live counter the monotone clause would
    # preserve — through.
    if not 0 <= last_seq <= MAX_SEQ:
        raise ValueError(
            f"the re-established last_seq {last_seq} is outside the event-store "
            f"integer domain (0 <= last_seq <= {MAX_SEQ}) — it came from the "
            f"carried value, from a replayed :GraphEvent.seq, or from a live "
            f"counter the fold above preserved, and a counter beyond that range "
            f"hands out seqs the range index cannot compare exactly (above "
            f"MAX_SEQ) or that no cursor can ever match (below zero), so the "
            f"events would be silently undeliverable")
    first_seq = min_seq if min_seq is not None else last_seq + 1
    if not 0 <= first_seq <= MAX_INT64:
        raise ValueError(
            f"the re-established first_seq {first_seq} (min replayed "
            f":GraphEvent.seq) is outside the event-store integer domain "
            f"(0 <= first_seq <= {MAX_INT64})")
    # The write is MONOTONE against a concurrent emitter. `next_seq` is an
    # atomic in-graph counter (`MERGE ... ON MATCH SET m.last_seq = m.last_seq
    # + 1`) and this leg runs on the LIVE graph after the wipe, so an emitter
    # can bump the counter between the reads above and this write. An
    # unconditional SET would then write a value BELOW the live one and hand
    # the next emitter a `seq` already in use — the collision this function
    # exists to prevent, reintroduced by the repair. `ON CREATE`/`ON MATCH`
    # with the `m.last_seq < $last` guard makes the write take the MAXIMUM, and
    # the RETURN reports what is ACTUALLY stored rather than what was proposed.
    # `first_seq` is raised, never lowered (see the docstring), and its `CASE`
    # reads the PRE-clause value — Cypher evaluates a SET clause against the
    # state before it.
    rows = proj.g.query(
        "MERGE (m:GraphEventMeta) "
        "ON CREATE SET m.last_seq = $last, m.first_seq = $first "
        "ON MATCH SET m.last_seq = CASE WHEN m.last_seq < $last THEN $last "
        "ELSE m.last_seq END, "
        "m.first_seq = CASE WHEN m.first_seq < $first THEN $first "
        "ELSE m.first_seq END "
        "RETURN m.last_seq",
        params={"last": last_seq, "first": first_seq},
    ).result_set
    if rows and rows[0][0] is not None:
        stored = int(rows[0][0])
        # The third input: a live counter the monotone clause preserved. It can
        # itself be out of domain, in which case nothing was written AND the
        # store is left as it was — refused loudly rather than silently
        # reported as repaired.
        if not 0 <= stored <= MAX_SEQ:
            raise ValueError(
                f"the stored :GraphEventMeta.last_seq is {stored}, outside the "
                f"event-store integer domain (0 <= last_seq <= {MAX_SEQ}) — a "
                f"live counter already held that value and this write is "
                f"monotone, so it was preserved rather than lowered; the "
                f"allocator must be repaired before it can be used")
        return stored
    return last_seq


def append_event(proj, seq: int, type_: str, payload: dict, event_id: str,
                 ts: str | None = None) -> bool:
    """Append a :GraphEvent node. Returns True on append, False on dup-skip.

    `payload` is the bare domain dict (JSON string); type/event_id/ts live as
    node properties (canonical) — codec encode/decode wiring is deferred to
    the first-upcaster task (#432 Task 4, plan-review P2).
    """
    ensure_event_schema(proj)
    ts = ts or _iso_now()
    # Dedup by event_id — APP-SIDE pre-check (FalkorDBLite has no
    # GRAPH.CONSTRAINT support; the unique constraint stays as the production
    # belt on FalkorDB Cloud, this check is the suspenders that also work in
    # tests). event_id is a server-side ULID, so a collision is a retry
    # artifact, never legitimate data — skip, never crash the mutation.
    existing = proj.g.query(
        "MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)",
        params={"id": event_id},
    ).result_set
    if existing and existing[0][0] > 0:
        logger.warning("event_store: duplicate event_id %r skipped", event_id)
        return False
    try:
        proj.g.query(
            "CREATE (e:GraphEvent {seq:$seq, ts:$ts, type:$type, "
            "event_id:$event_id, payload:$payload})",
            params={
                "seq": int(seq), "ts": ts, "type": type_,
                "event_id": event_id, "payload": json.dumps(payload, ensure_ascii=False),
            },
        )
        return True
    except Exception:  # noqa: BLE001, RUF100
        # Belt-and-suspenders: if the store's unique constraint fired (or any
        # other write error), never crash the mutation — the read path dedups.
        logger.warning("event_store: append failed for event_id %r — skipped", event_id)
        return False


def read_after(proj, after_seq: int, types: list[str] | None = None,
               limit: int = 100) -> list[dict]:
    """Return events with seq > after_seq, ordered seq ASC, deduped by event_id.

    `types` filters by event type if provided. `limit` caps the page
    (default 100, max 1000). Dedup: a duplicate event_id (defense in depth —
    the unique constraint normally prevents them) keeps the FIRST occurrence.
    """
    limit = max(1, min(int(limit), 1000))
    params: dict = {"after": int(after_seq), "limit": limit}
    if types:
        params["types"] = list(types)
        where = " AND e.type IN $types"
    else:
        where = ""
    rows = proj.g.query(
        "MATCH (e:GraphEvent) WHERE e.seq > $after" + where + " "
        "RETURN properties(e) ORDER BY e.seq ASC LIMIT $limit",
        params=params,
    ).result_set
    out: list[dict] = []
    seen: set[str] = set()
    for (props,) in rows:
        eid = props.get("event_id")
        if eid and eid in seen:
            continue
        if eid:
            seen.add(eid)
        if "payload" in props and isinstance(props.get("payload"), str):
            try:
                props = dict(props)
                props["payload"] = json.loads(props["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        out.append(props)
    return out


def purge_expired(proj, retention_days: int = 30) -> int:
    """Delete :GraphEvent nodes older than `retention_days` (ISO8601 ts cutoff).

    Per-graph = per-org — no org_id filter (plan-review P2). Idempotent.
    Returns the number of deleted nodes.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat()  # noqa: UP017
    rows = proj.g.query(
        "MATCH (n:GraphEvent) WHERE n.ts < $cutoff "
        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(*)",
        params={"cutoff": cutoff},
    ).result_set
    deleted = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    _refresh_first_seq(proj)
    return deleted


def purge_overflow(proj, max_events: int) -> int:
    """Enforce a per-org size cap: delete the OLDEST events over `max_events`.

    Returns the number of deleted nodes.
    """
    rows = proj.g.query("MATCH (n:GraphEvent) RETURN count(n)").result_set
    total = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    overflow = total - int(max_events)
    if overflow <= 0:
        return 0
    del_rows = proj.g.query(
        "MATCH (n:GraphEvent) WITH n ORDER BY n.seq ASC LIMIT $overflow "
        "DETACH DELETE n RETURN count(*)",
        params={"overflow": overflow},
    ).result_set
    deleted = int(del_rows[0][0]) if del_rows and del_rows[0][0] is not None else 0
    _refresh_first_seq(proj)
    return deleted


def _refresh_first_seq(proj) -> None:
    """Update the GraphEventMeta first_seq watermark after a purge.

    first_seq = min surviving seq, or last_seq + 1 when the graph is empty
    (every cursor below the next write is then expired). Lets events_poll
    return 410 even when the graph has been fully purged.
    """
    try:  # noqa: SIM105
        proj.g.query(
            "MATCH (m:GraphEventMeta) "
            "OPTIONAL MATCH (e:GraphEvent) "
            "WITH m, min(e.seq) AS mn "
            "SET m.first_seq = coalesce(mn, m.last_seq + 1)"
        )
    except Exception:  # noqa: BLE001, RUF100
        pass  # best-effort watermark
