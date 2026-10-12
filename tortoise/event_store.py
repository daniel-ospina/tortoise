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
# * INT64 (the wrap ceiling). The bump is `m.last_seq + 1` in 64-bit
#   arithmetic, so a counter at INT64_MAX would WRAP to INT64_MIN — there is no
#   saturating step in the engine (reproduced on FalkorDB 4.20.4).
#
# Both ceilings are enforced ON THE ALLOCATOR by ``next_seq`` (#5379): a bump
# that would leave the domain is REFUSED (``ValueError``) rather than written,
# so no undeliverable ``seq`` can be minted at all, and the counter is left
# where it is for the repair path to fix. Enforcing it at the reference point is
# what makes the domain an invariant of the STORE rather than of the two rebuild
# boundaries above — they can only bound what a wipe CARRY, never what a live
# emitter is handed.
#
# ``first_seq`` is NOT bumped and is compared app-side in `poll_events` (never
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

    #5379 — the bump REFUSES (``ValueError``) rather than writing a ``last_seq``
    outside the store's integer domain ``0 .. MAX_SEQ``. Without this the
    allocator had no ceiling, and BOTH ways out of the domain are undeliverable
    through `read_after`'s `seq > cursor` — the #4653 harm (an event that exists
    and is never delivered, with no 410 to tell the subscriber):

    * Above ``MAX_SEQ`` the `seq` range index compares in double precision, so
      the seq is silently skipped by a cursor that landed just below it (§
      ``MAX_SEQ``).
    * At INT64_MAX the bump WRAPS NEGATIVE, and a negative seq can never satisfy
      `seq > cursor` for any cursor the cursor codec accepts
      (`_decode_cursor` rejects negatives).

    Refusing on the ALLOCATOR — not capping the stored value — is the point: a
    capped-but-returned seq would hand the emitter a seq the store ALREADY
    issued, which is the #4653 collision (two events, one ordering key), and a
    `CASE` that saturates `last_seq` would keep the counter permanently at the
    top so every later emit reused it.

    A counter ALREADY outside the domain — negative (e.g. after a bump the
    pre-#5379 code wrapped), or above ``MAX_SEQ`` (a hand-written or restored
    one) — is refused too, and left UNCHANGED rather than bumped or repaired:
    a refusal must not move the allocator, and re-seeding it here could
    re-issue the seq of an existing `:GraphEvent` (``reestablish_watermark``
    owns repair). A property of another type is refused, never coerced:
    `'5' + 1` cannot be compared, so reporting a seq the node does not hold
    would be a false success (the same refusal `reestablish_watermark` makes).

    The refusal cannot race a concurrent emitter because the bump and the grant
    decision are ONE query. The `granted` marker is computed from the counter's
    PRE-increment value (`prev`, bound before the `SET`), which is the only way
    to tell "this call handed out ``MAX_SEQ``" from "the counter was already at
    ``MAX_SEQ`` and nothing moved" — both leave `last_seq` at ``MAX_SEQ``
    afterwards. Concurrent emitters therefore split the remaining domain
    exactly: each granted bump moves the counter, so the last legal seq is
    handed out once (the live counter test pins it).
    """
    rows = proj.g.query(
        # `ON CREATE SET m.last_seq = 0` seeds the PRE-increment value, so the
        # one CASE below grants `1` to a fresh graph exactly as the pre-#5379
        # `ON CREATE SET m.last_seq = 1` did; the node is never observable at 0
        # (the seed and the SET are one query, and the counter is only ever
        # visible through this call's RETURN, which sees the post-SET value).
        #
        # `prev >= 0 AND prev < $max` — never `$max > prev`. FalkorDB 4.20.x
        # compares an int64 property holding the wrapped `-9223372036854775807`
        # region (and a parameter as extreme as INT64_MAX) as NEITHER `<` nor
        # `>=` against a positive constant, so the reversed form would read a
        # negative counter as "below the cap" and grant it. The form below is
        # verified correct for every int64 region a counter can hold (#5379).
        "MERGE (m:GraphEventMeta) "
        "ON CREATE SET m.last_seq = 0, m.first_seq = 1 "
        "WITH m, m.last_seq AS prev "
        "SET m.last_seq = CASE WHEN prev >= 0 AND prev < $max "
        "THEN prev + 1 ELSE prev END "
        "RETURN m.last_seq, prev >= 0 AND prev < $max AS granted",
        params={"max": MAX_SEQ},
    ).result_set
    seq, granted = rows[0] if rows else (None, None)
    # `bool` is excluded for the same reason `_validate_event_meta_entry`
    # excludes it: it is an int subclass, and a boolean counter is a corrupt
    # property, not a position.
    if isinstance(seq, bool) or not isinstance(seq, (int, float)):
        raise ValueError(
            f"the :GraphEventMeta.last_seq is {seq!r} ({type(seq).__name__}), "
            f"not a number — the allocator cannot be bumped and must be "
            f"repaired before use (#3902's `_restore_event_meta` fallback owns "
            f"the legacy shape)")
    if granted is not True:
        raise ValueError(
            f"the event-store seq allocator is exhausted: "
            f":GraphEventMeta.last_seq is {int(seq)} and an allocation needs "
            f"0 <= last_seq < {MAX_SEQ}; the next seq it would hand out is "
            f"undeliverable — above {MAX_SEQ} the `seq` range index no longer "
            f"compares exactly (double precision), and a negative counter can "
            f"never satisfy `seq > cursor`. Refusing to mint it (#5379): the "
            f"counter must be repaired BY HAND before emitting — this allocator "
            f"only ever raises `last_seq`, and `reestablish_watermark` REFUSES "
            f"an out-of-domain counter instead of lowering it, so neither can "
            f"repair this state")
    return int(seq)


def capture_watermark(proj) -> dict | None:
    """Read the per-graph ``:GraphEventMeta`` HIGH-WATER MARK, or ``None``.

    #4653: returned as a sidecar-shaped entry (``{"last_seq": int}``) so a
    pre-wipe capture can be carried through a wipe+replay. ``None`` — never
    ``{"last_seq": 0}`` — when no counter node exists: a graph that has never
    emitted an event must come back with no counter, so the next
    ``next_seq`` creates it at 1 exactly as it always did. Inventing a
    counter here would make "never emitted" indistinguishable from "emitted
    an event with seq 0", and ``poll_events``'s ``after_seq == 0`` sentinel
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
    which lets ``poll_events`` answer 410 instead of silently returning ``[]``
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

    A counter node missing one of its two properties is the legacy shape
    ``hosted_backup._restore_event_meta`` (#3902) already repairs by falling
    back to ``last_seq + 1`` for a missing ``first_seq``, so this write REPLACES
    a NULL rather than preserving it — the monotone clause is ``IS NULL OR <``
    for exactly that reason. A property of some OTHER type (a string, a list)
    cannot be compared and is left in place by Cypher's ``CASE``, so it is
    REFUSED after the write instead: reporting the proposed value for it would
    claim a repair that never happened (a NULL ``last_seq`` was reported as a
    restored watermark, and the next ``next_seq`` then evaluated ``null + 1``).

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
    #
    # The guard is `IS NULL OR <`, never a bare `<`: #3902's sibling already
    # treats a counter missing one of its two properties as a legacy shape
    # (`_restore_event_meta` falls back to `last_seq + 1` for a missing
    # `first_seq`), and a bare `<` against NULL evaluates to NULL — so the ELSE
    # branch would KEEP the NULL, the write would report a value it did not
    # store, and a NULL `last_seq` would then make `next_seq` evaluate
    # `null + 1`. NULL is the one live value that must be REPLACED, not
    # preserved: it carries no position to preserve.
    # `first_seq` is raised, never lowered (see the docstring), and its `CASE`
    # reads the PRE-clause value — Cypher evaluates a SET clause against the
    # state before it.
    rows = proj.g.query(
        "MERGE (m:GraphEventMeta) "
        "ON CREATE SET m.last_seq = $last, m.first_seq = $first "
        "ON MATCH SET m.last_seq = CASE WHEN m.last_seq IS NULL "
        "OR m.last_seq < $last THEN $last ELSE m.last_seq END, "
        "m.first_seq = CASE WHEN m.first_seq IS NULL "
        "OR m.first_seq < $first THEN $first ELSE m.first_seq END "
        "RETURN m.last_seq",
        params={"last": last_seq, "first": first_seq},
    ).result_set
    raw = rows[0][0] if rows else None
    # A NON-NUMERIC stored value is refused, not coerced. `bool` is excluded for
    # the same reason `_validate_event_meta_entry` excludes it. Reaching here
    # means the monotone clause could not compare the live property (Cypher's
    # `'5' < $last` is neither true nor false, so the ELSE branch kept it) or the
    # driver returned no row — and returning the PROPOSED `last_seq` for either
    # (the pre-review behaviour) reported a repair that never happened.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(
            f"the :GraphEventMeta write stored {raw!r} "
            f"({type(raw).__name__}), not a numeric last_seq — the monotone "
            f"clause only compares numbers, so a counter property of another "
            f"type is preserved rather than replaced and the store is left "
            f"without a usable allocator; repair the counter node before "
            f"rebuilding")
    stored = int(raw)
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
    (every cursor below the next write is then expired). Lets poll_events
    return 410 even when the graph has been fully purged.

    #5379 — the empty-log floor is computed so the addition can neither WRAP
    nor leave the `first_seq` domain ``0 .. MAX_INT64``. This runs after a
    purge, i.e. against the counter as it is ON DISK, which can be one a
    previous build already wrapped to `INT64_MIN` or a hand-written value above
    ``MAX_SEQ``: ``next_seq`` refuses to bump such a counter but does not repair
    it, and a purge does not either. A bare `m.last_seq + 1` at INT64_MAX wrapped
    to `INT64_MIN`, so the floor became NEGATIVE and `poll_events`'s
    `after_seq < first_seq` guard could never fire (no accepted cursor is below
    a negative floor) — a subscriber parked above a truncated stream then read
    `[]` forever instead of getting the truthful 410, which is the #4653 harm
    reached through the watermark rather than through the allocator. The
    overflow is detected by the wrap itself (`m.last_seq + 1 < 0`), NOT by a
    comparison against an INT64_MAX bound — FalkorDB 4.20.x mis-evaluates those
    (see `next_seq`) — and the floor is saturated at ``MAX_INT64``, the top of
    the domain and legitimately ABOVE ``MAX_SEQ`` (§ ``MAX_SEQ``).

    A counter ALREADY below the domain saturates the same way, and the reason is
    the 410 contract itself: ``next_seq`` REFUSES such a counter, so its next
    write is unreachable, and every accepted cursor (every cursor is `>= 1`;
    `after_seq == 0` is the "from the start" sentinel that SKIPS the guard) is
    genuinely expired. Raising the floor to ``0`` — as an earlier draft did —
    left `poll_events`' `after_seq < first_seq` test unable to fire for ANY
    reachable cursor (`1 < 0` is false), so a subscriber parked above the
    truncated stream still read `[]` forever with no 410: the exact #4653 harm
    this change exists to remove, just reached through the already-negative
    branch instead of the wrap branch. Saturation makes both out-of-domain
    inputs produce a floor above every cursor, so both answer 410.
    """
    try:  # noqa: SIM105
        proj.g.query(
            "MATCH (m:GraphEventMeta) "
            "OPTIONAL MATCH (e:GraphEvent) "
            "WITH m, min(e.seq) AS mn, "
            "CASE WHEN m.last_seq < 0 THEN $max_int64 "
            "WHEN m.last_seq + 1 < 0 THEN $max_int64 "
            "ELSE m.last_seq + 1 END AS nxt "
            "WITH m, coalesce(mn, nxt) AS floor "
            "SET m.first_seq = CASE WHEN floor < 0 THEN 0 ELSE floor END",
            params={"max_int64": MAX_INT64},
        )
    except Exception:  # noqa: BLE001, RUF100
        pass  # best-effort watermark
