"""Durable per-tenant event store — `:GraphEvent` nodes in the team's graph.

#432 (subscriptions + claim lifecycle): the SDK emit hook writes graph-change
events here. The graph namespace IS the team partition — there is NO `team_id`
property (plan-review P2): the SDK writes into its own graph, and REST/MCP
isolation comes from the namespace (server-derived, never client-supplied).

Schema (idempotent; mirror sdk.py `_ensure_registry_indexes` pattern):
- exact-match index on `event_id` FIRST, then a unique constraint
  (FalkorDB requires the index before the constraint)
- plain index on `seq` (per-graph = per-team cursor reads)

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


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    except Exception:  # noqa: BLE001
        logger.debug("event_store: event_id index may already exist")
    try:
        g.query(
            "GRAPH.CONSTRAINT CREATE tortoise_ge_uid UNIQUE NODE GraphEvent "
            "PROPERTIES 1 event_id"
        )
    except Exception:  # noqa: BLE001
        logger.debug("event_store: unique constraint may already exist")
    try:
        g.query("CREATE INDEX FOR (n:GraphEvent) ON (n.seq)")
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
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

    Per-graph = per-team — no team_id filter (plan-review P2). Idempotent.
    Returns the number of deleted nodes.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=int(retention_days))).isoformat()
    rows = proj.g.query(
        "MATCH (n:GraphEvent) WHERE n.ts < $cutoff "
        "WITH n LIMIT 10000 DETACH DELETE n RETURN count(*)",
        params={"cutoff": cutoff},
    ).result_set
    deleted = int(rows[0][0]) if rows and rows[0][0] is not None else 0
    _refresh_first_seq(proj)
    return deleted


def purge_overflow(proj, max_events: int) -> int:
    """Enforce a per-team size cap: delete the OLDEST events over `max_events`.

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
    try:
        proj.g.query(
            "MATCH (m:GraphEventMeta) "
            "OPTIONAL MATCH (e:GraphEvent) "
            "WITH m, min(e.seq) AS mn "
            "SET m.first_seq = coalesce(mn, m.last_seq + 1)"
        )
    except Exception:  # noqa: BLE001
        pass  # best-effort watermark
