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

⛔ EVERY OTHER LOSS IS NAMED (#9208). A store write that is REFUSED (e.g.
§17.1's third door refusing an undeclared write) or that fails for any other
reason must never be swallowed into the same no-op as a duplicate: the feed
would simply stop while `poll_events` kept answering `[]`, making "quiet" and
"dead" the same reply. Such a failure is recorded here as a NAMED degraded
state (`record_degraded`/`degraded`), and `poll_events` REFUSES to answer while
it is set. The state is deliberately NOT cleared by a later successful write:
the events in the gap never reached the store, so a subscriber parked below
them would silently under-read forever. Acknowledging it is explicit
(`clear_degraded`).

⚠️ TWO SCOPES, STATED so neither is over-read. It is keyed by STORE + GRAPH
(the `:GraphEvent` namespace IS the org partition, so one tenant's dead feed
must not refuse another tenant's poll, and two databases are two stores even
under one name), and it lives in PROCESS MEMORY — a peer process cannot see it,
and a peer process cannot ACKNOWLEDGE it either (the acknowledgement is a Python
call in the process that observed the loss; from outside, the only exit is a
restart of that worker). What it does guarantee is the property the issue
targets — **a `poll_events` caller in a process that emitted for a graph cannot
be told "nothing happened" over lost events.**

⚠️ AND ONE CONSUMER IS NOT COVERED, named rather than implied: the webhook
delivery sweep (`webhook_delivery.dispatch_pending`) reads `read_after`
directly, so its `if not events: continue` still treats an empty page as
"caught up". Wiring
the degraded state into the delivery/retry state machine changes a
delivery-loss decision that `#8098` deliberately keeps in the dispatch state
machine, so it is a follow-up, not a silent part of this fix.

§17.1 — THIS MODULE DECLARES ITS OWN LANE (#9124 part 1). Every mutating
statement here goes through the projection's write handle (`proj.g`), which since
#9124 DETECTS a write that arrives with no declared lane. The store is a SECOND
durable store rather than a projection of the JSONL journal, so it belongs to
neither the live nor the replay lane:

* `_graph_metadata_write_scope` for the store's own bookkeeping — the schema DDL
  (`ensure_event_schema`), the `:GraphEventMeta` allocator (`next_seq`), the
  append (`append_event`) and the retention purges. That lane states two
  obligations and both hold, spelled out at `next_seq`/`append_event`: no journal
  record describes a `:GraphEvent` row (a payload-only emit writes one with no
  JSONL record at all — see `capture_watermark`), and a rebuild does not replay
  the rows (#4664): it CARRIES the counter (`capture_watermark` →
  `reestablish_watermark`) and raises `first_seq`, so a parked subscriber gets
  the truthful 410 instead of `[]` forever. A declared truncation with a cursor
  contract, not corruption.
* the watermark in `reestablish_watermark`, whose lane is stated by its CALLER
  and opened HERE, at the handle: `rebuild_all` puts back the value it CAPTURED
  before the wipe and names that leg (`pre-wipe-restore: …`, `#9210`), while
  the journal engine `rebuild()` — which ALSO puts back a captured value, taken
  by `capture_watermark` before its own wipe — keeps the engine's method-wide
  `"replay"` and is a DEFERRED instance of the same defect, left to `#9213`
  rather than claimed truthful (it was claimed to "re-derive" the value from
  the journal, which the code refutes; see the helper's docstring). The
  declaration has to live at the write because an inner scope SHADOWS an outer
  one (`_cache_write_authorized` is a `ContextVar`), so a `with` at the call
  site could not state the lane at all.

⚠️ The declarations are NOT a licence for the swallow: `append_event` and
`_refresh_first_seq` still catch bare `Exception` (a store failure must never
crash the mutation), so an UNDECLARED write here would degrade with only a
WARNING as its trace. #9208 landed the NAMED degraded mode above; what is still
missing is the DOOR that sets it when a write is REFUSED — that is arming the
guarded handle (#9124 part 2), not a declaration.
"""
from __future__ import annotations

import functools
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class ChangeFeedDegraded(RuntimeError):
    """The `:GraphEvent` change feed is KNOWN-INCOMPLETE in this process.

    A NAMED degraded mode (#9208), the shape `#8577`'s capture drain uses,
    rather than a silent no-op. Raised by `poll_events` when a change-feed
    write has been refused or lost: a short page is then indistinguishable from
    a quiet stream, so answering at all would report success over lost events.

    The alternative the issue also allowed — letting the refusal propagate out
    of the emitting door — was rejected because `_emit_event` is best-effort BY
    CONTRACT and many doors emit AFTER their cache write (`delete_point`,
    `promote_point`, the dedupe folds), so a raise there would report a failed
    mutation that actually landed. The FEED's consumer is the party that can
    act on a dead feed, so the signal belongs there.
    """


#: The named degraded state of a GRAPH's change feed (#9208), keyed by
#: ``(store identity, graph name)``. Empty = no change-feed write has been
#: refused/lost in this process. A dict per store+graph, not a bool, so the
#: refusal can NAME the diagnosis instead of only the symptom.
#:
#: KEYED BY STORE + GRAPH, not process-global. The `:GraphEvent` store is
#: per-graph (the namespace IS the org partition), so a dead feed on one tenant
#: must not refuse another tenant's poll; and the identity half is needed because
#: the graph name alone does not name a STORE — two embedded databases both called
#: `tortoise` are different stores. A process-global flag also latched a whole
#: session on any deliberate, expected refusal (a suite driving the #5379
#: allocator ceiling on purpose), which is the same over-read in the other
#: direction.
_DEGRADED: dict[tuple[str, str], dict[str, Any]] = {}

#: Guards `_DEGRADED`, because it is touched from BOTH thread populations.
#:
#: `record_degraded` runs off the event loop on the hosted path — the write is
#: offloaded through `hosted_api._graph_offload` (→ `_cp_offload` →
#: `monitoring.run_control_plane_call`) onto the dedicated #3498 `graph` pool,
#: NOT `asyncio.to_thread`, whose shared default executor is the #3060
#: starvation path the repo avoids — so the `:GraphEvent` emit leg executes on a
#: graph-pool worker, while `degraded_graphs()` is called from the `async def`
#: health route ON the loop (`hosted_api.health()` → `_change_feed_health`). `sorted(_DEGRADED.items())` materialises a list
#: while a peer thread may insert, which raises ``RuntimeError: dictionary
#: changed size during iteration`` — and `/health` swallows that, so it would
#: answer ``change_feed.degraded: false`` at the exact moment a second feed went
#: degraded, on the only out-of-process view of the state. The same lock makes
#: `lost` an atomic read-modify-write; the count is QUOTED TO THE CONSUMER
#: ("N event(s) are unaccounted for"), so an undercount is a false number about
#: data loss rather than a cosmetic one.
_DEGRADED_LOCK = threading.Lock()


@functools.cache
def _canonical_path(path: str) -> str:
    """ONE store, ONE key — however its path is spelled.

    Keying on the raw string splits the state for a single database reached
    through a symlinked deployment directory, a `~`-spelled home, or a relative
    path: a refused write recorded under one spelling would leave a poll
    through the other answering `[]` over the lost event — the exact silence
    this module exists to break. So the path half uses the repo's canonical
    identity, the same normalisation `projection._prewipe_db_path_identity` and
    `fallback_snapshot.snapshot_key` apply (`realpath(abspath(expanduser()))`,
    which is what makes macOS's `/tmp` -> `/private/tmp` compare EQUAL).

    ⛔ MEMOISED per raw spelling, and the memo is part of the fix, not an
    optimisation. The normalisation reads MUTABLE ambient state — the process
    cwd (`abspath`) and the symlinks along the path (`realpath`) — while both
    the WRITER (`record_degraded` -> `_graph_key`) and the READER
    (`degraded()` -> `_graph_key`) re-derive the key on every call. Re-deriving
    would let a recorded loss become un-findable by the very poll it must
    refuse: `chdir` after a relative `_path` was recorded, or a repointed
    symlinked parent, moves the key out from under the state, `degraded()`
    answers `None`, and `poll_events` returns `[]` over the lost event — the
    silence this module exists to break, reintroduced by the fix for a
    different one. Pinning the resolution for the process lifetime makes a
    projection's identity stable, which is what the raw-string key was, and
    takes the per-poll `realpath` syscalls off the `poll_events` path.

    `:memory:` is left ALONE deliberately: it names no file, `abspath` would
    fold the caller's cwd into the key, and two cwds would then be two keys for
    the one embedded daemon — an arbitrary identity is worse than a shared one.
    """
    if path == ":memory:":
        return path
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(path)))
    except (OSError, ValueError):  # pragma: no cover — defensive
        return str(path)


def _store_identity(proj) -> str:
    """Which STORE this projection talks to — its file, or its HOST:PORT.

    The PORT is part of the identity, not decoration. One host can carry several
    FalkorDB servers, and this repo's own environment runs two on loopback —
    `localhost:6379` (the test matrix) and `localhost:16379` (canonical/dev) —
    both with the default graph name `tortoise`. Keyed on the host alone, a
    refused write on one would permanently refuse `poll_events` on the other.

    The port is read from the connection pool, the same seam
    `tests/_embedded._projection_host` uses: `FalkorProjection` records `_host`
    but not `_port`, and the client keeps it in
    `connection.connection_pool.connection_kwargs`. Every step is `getattr`-guarded
    because callers reach here with lightweight projection stubs, and `None`
    simply degrades to the host-only key rather than raising from an error path.
    """
    path = getattr(proj, "_path", None)
    if path:
        return f"file:{_canonical_path(str(path))}"
    host = getattr(proj, "_host", None)
    if not host:
        return ""
    port = getattr(proj, "_port", None)
    if port is None:
        pool = getattr(getattr(getattr(proj, "db", None), "connection", None),
                       "connection_pool", None)
        kwargs = getattr(pool, "connection_kwargs", None)
        port = kwargs.get("port") if isinstance(kwargs, dict) else None
    return f"host:{host}:{port}" if port is not None else f"host:{host}"


def _graph_key(proj) -> tuple[str, str]:
    """The store partition key: (store identity, graph name)."""
    return (_store_identity(proj), str(getattr(proj, "graph_name", None) or ""))


def record_degraded(proj, stage: str, event_type: str, event_id: str | None,
                    exc: BaseException) -> dict[str, Any]:
    """Record that a `:GraphEvent` write was refused or lost (#9208).

    `stage` names the statement that failed, because the defects share a symptom
    but not a diagnosis — and a reader must be told WHICH one it is, not only
    that something went wrong:

    * ``"emit"`` — the hook's own leg (the `seq` allocator, the schema DDL);
    * ``"append"`` — the row write, and the store PROVABLY does not hold it
      (the post-failure re-read found nothing), so the event is LOST;
    * ``"append-unverified"`` — the row write failed AND the store could not be
      read back, so the row may or may not have landed. Reported as a guess
      about nothing would be the same collapse this function exists to undo.

    ``lost`` counts the events this graph has lost in this process; it only ever
    grows, matching the fact that those rows can never be delivered.

    ``stage``/``event_type``/``error`` describe the MOST RECENT failure, and the
    FIRST one is kept beside them as ``first_stage``/``first_error``: a graph can
    fail twice for different reasons (`append` then `append-unverified`), and the
    first is often the only provable one — the order matters, because
    ``append-unverified`` is by definition the stage that could NOT be proven —
    so overwriting it would discard the diagnosis a reader needs. Both are
    recorded rather than one being chosen.

    Called from BOTH swallows (`TortoiseSDK._emit_event` and `append_event`) so
    the two cannot record different diagnoses for the same failure, and so a
    reader has one place to look.
    """
    key = _graph_key(proj)
    error = f"{type(exc).__name__}: {exc}"[:400]
    with _DEGRADED_LOCK:
        previous = _DEGRADED.get(key)
        state = {
            "graph": key[1],
            "stage": stage,
            "event_type": event_type,
            "event_id": event_id,
            "error": error,
            "first_stage": stage if previous is None else previous["first_stage"],
            "first_error": error if previous is None else previous["first_error"],
            "lost": 1 if previous is None else int(previous["lost"]) + 1,
        }
        _DEGRADED[key] = state
        return state


def degraded(proj) -> dict[str, Any] | None:
    """This graph's named degraded state, or ``None`` when its feed is healthy.

    Deliberately LOCK-FREE, unlike the three writers under `_DEGRADED_LOCK`: a
    single `dict.get` is atomic under the GIL and under free-threaded CPython's
    per-object dict locks, and `record_degraded` PUBLISHES a freshly built dict
    (never mutates one in place), so a reader here always sees a whole state.
    Taking the lock would also serialise every `poll_events` call against an
    emitting thread's error path for no gain. Keep it that way: this is a single
    lookup, NOT a read-modify-write (the writers are the read-modify-writes).
    """
    return _DEGRADED.get(_graph_key(proj))


def degraded_graphs() -> list[dict[str, Any]]:
    """EVERY degraded feed in this process — the read side for surface reports.

    `degraded(proj)` needs a projection, which a reporting surface (``/health``)
    does not have; the key is `(store, graph)` and only a projection can compute
    it, so the listing carries `store` alongside the state. Copies, never the
    live dicts, so a reporter cannot mutate the state by holding one.

    Sorted by `(store, graph)` so a report is stable across calls (a dict order
    would make an operator diff meaningless).
    """
    # The SNAPSHOT is taken under the lock and the list is built outside it:
    # sorting/building under the lock would hold an emitting thread's error path
    # behind a health reporter. `sorted` on a private list cannot resize under us.
    with _DEGRADED_LOCK:
        snapshot = list(_DEGRADED.items())
    return [
        {
            "store": store,
            "graph": state["graph"],
            "stage": state["stage"],
            "event_type": state["event_type"],
            "first_stage": state["first_stage"],
            "lost": state["lost"],
            "error": state["error"],
            # Both diagnoses, for the same reason `degraded()` carries both: a
            # reporter that forwarded only the CURRENT stage would still hide
            # the first, often only provable, one (#9208 review).
            "first_error": state["first_error"],
        }
        for (store, _graph), state in sorted(snapshot)
    ]


def clear_degraded(graph_name: str | None = None) -> None:
    """Acknowledge the loss and re-enable polling (#9208).

    Explicit on purpose: the events in the gap were never written, so a
    successful write afterwards does not repair them — clearing on success
    would restore the silence the state exists to break. An operator (or a
    replay from the tail) is the acknowledgement.

    ``graph_name=None`` clears EVERY store+graph in this process (the operator's
    "I have dealt with this", and the test suite's isolation); naming one graph
    clears that graph in every store.

    ⚠️ THIS IS A PYTHON CALL IN THE PROCESS THAT HOLDS THE STATE, and there is
    no REST/MCP/CLI surface for it yet (#9208's recorded residual): the state is
    process memory, so a peer process — including an operator's shell on the same
    host — cannot clear it, and neither can the HTTP client that receives the
    503. The exits are (a) this call inside that process, or (b) restarting that
    worker. `degraded_graphs()` is what makes the state visible from outside
    (``GET /health`` → ``change_feed``), so an operator can at least SEE which
    worker needs it.
    """
    with _DEGRADED_LOCK:
        if graph_name is None:
            _DEGRADED.clear()
            return
        for key, state in list(_DEGRADED.items()):
            if state["graph"] == str(graph_name):
                _DEGRADED.pop(key, None)


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
    # Lazy import: `tortoise.projection` imports THIS module lazily for the same
    # reason (see its `capture_watermark` call sites), so neither direction can
    # form an import cycle.
    from .projection import _graph_metadata_write_scope
    g = proj.g
    # §17.1 (#9124): DDL IS GRAPH METADATA — no journal record describes an
    # index and a rebuild recreates it, so the schema sweep declares the
    # graph-metadata lane instead of arriving at the write handle undeclared.
    # Same reasoning as `FalkorProjection._ensure_indexes`.
    with _graph_metadata_write_scope("event-store-schema"):
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
    # §17.1 (#9124): the ALLOCATOR is graph metadata. Nothing happened to the
    # corpus that a replay could re-apply — the counter is an ordering BOUND,
    # carried across a rebuild by `capture_watermark`/`reestablish_watermark`
    # rather than recomputed — so this declares the graph-metadata lane, the same
    # declaration `_advance_ep_version` makes for `:EpMeta` (#9123).
    from .projection import _graph_metadata_write_scope
    with _graph_metadata_write_scope("event-store-seq"):
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


def reestablish_watermark(proj, carried_last_seq: int | None, *,
                          restore_reason: str | None = None) -> int | None:
    """Re-establish ``:GraphEventMeta`` after a wipe+replay (#4653).

    ``restore_reason`` states which lane the carried value came from, and it is
    the CALLER's to state because this helper has two callers with unlike
    authorization: `FalkorProjection.rebuild_all` puts back a value it CAPTURED
    from this graph immediately before the wipe, so it names its restore leg
    (``pre-wipe-restore: :GraphEventMeta watermark (#4653)``, #9210) — the replay
    lane's contract ("this content came FROM the record") is FALSE for it. The
    journal engine `rebuild()` puts back a captured value TOO — it calls
    `capture_watermark` before its own wipe and hands the result here — so the
    same contract is false for it as well; it keeps the default and therefore
    keeps this module's landed declaration (#9224) as a DEFERRED instance of
    that defect, disclosed here and left to `#9213`, NOT because the value
    comes from the replayed log (it does not: the carry is a pre-wipe read of
    the live counter). ⚠️ A blanket restore reason is not the honest fix for
    that caller either: `last_seq` is `max(carried, max(:GraphEvent.seq))`, so
    the stored value can be the REPLAYED seq when it is higher — the two
    sources have to be split before one lane can describe the write. The lane
    is opened at the write below, never at the call site: the
    scope is a `ContextVar`, so an inner declaration shadows an outer one, and a
    `with` around the call would be silently discarded — the exact way the
    watermark went on declaring `"replay"` while restoring captured state.

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
    # §17.1 (#9124, #9210, #9224): this write never arrives at the handle
    # undeclared, but WHICH lane it declares is the caller's to state — this
    # lane's own named restore leg when `rebuild_all` is putting back the value
    # it captured before the wipe (#9210), the engine's replay lane for
    # `rebuild()`, which restores a captured counter as well but keeps the
    # `"replay"` default as a DEFERRED #9213 item (not because its value is
    # journal-derived — see the docstring; a blanket restore reason would be
    # false for the `max(carried, max(:GraphEvent.seq))` leg).
    # `_cache_write_authorized` is the same single-writer ContextVar
    # every scope uses, so an undeclared caller still fails closed on `None`;
    # the lane is opened here because an inner scope shadows an outer one, so a
    # `with` at the call site could never be the declaration.
    # ⛔ The replay lane is declared through the HANDLE-ONLY helper, and its name
    # is IMPORTED rather than re-spelled (`#9391`). Two reasons, both measured by
    # that issue's review: the reason string is now the KEY the raw-query lane's
    # narrowing matches on, so a second literal here would be a second definition
    # of the lane; and a bare `_cache_write_authorized(_REPLAY_WRITE_LANE)` carries
    # no closure, so a lane nested inside this window would re-open the raw-query
    # lane — the exact shape `_replay_write_scope()` exists to prevent. This is
    # the SECOND `replay` declaration site in the tree (the other is
    # `_replay_write_scope()`), so it goes through the same helper.
    # A caller-supplied restore reason keeps the ordinary lane: those lanes are
    # not handle-only, and narrowing them is not this seam's decision.
    from .projection import (
        _REPLAY_WRITE_LANE,
        _cache_write_authorized,
        _handle_only_write_scope,
    )
    scope = (_handle_only_write_scope(_REPLAY_WRITE_LANE) if not restore_reason
             else _cache_write_authorized(restore_reason))
    with scope:
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
    """Append a :GraphEvent node. True on append; False on a dup-skip OR a
    refused/failed write (the two are told apart below — do not read `False` as
    "nothing happened").

    `payload` is the bare domain dict (JSON string); type/event_id/ts live as
    node properties (canonical) — codec encode/decode wiring is deferred to
    the first-upcaster task (#432 Task 4, plan-review P2).

    ``False`` therefore means ONE of two things, and the caller must not read it
    as "nothing happened": a duplicate (no loss) or a REFUSED/failed write (this
    event is LOST). The second is recorded as the named degraded state (#9208)
    that ``degraded()`` reports and ``poll_events`` refuses to answer over; it is
    not raised, because `_emit_event` is best-effort by contract.
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
    # §17.1 (#9124): the APPEND is graph metadata, declared. The row is not a
    # projection of a JSONL record — a payload-only emit writes one with no JSONL
    # record at all — and a rebuild does not replay it (#4664); the module
    # docstring states both obligations and the cursor contract (`first_seq` is
    # raised so a parked subscriber reads a truthful 410, never `[]` forever).
    from .projection import _graph_metadata_write_scope
    try:
        with _graph_metadata_write_scope("event-store-append"):
            proj.g.query(
                "CREATE (e:GraphEvent {seq:$seq, ts:$ts, type:$type, "
                "event_id:$event_id, payload:$payload})",
                params={
                    "seq": int(seq), "ts": ts, "type": type_,
                    "event_id": event_id, "payload": json.dumps(payload, ensure_ascii=False),
                },
            )
        return True
    except Exception as exc:  # noqa: BLE001, RUF100
        # Belt-and-suspenders: never crash the mutation on a store write error
        # — the read path dedups, and `_emit_event` is best-effort by contract.
        #
        # #9208: but it must NOT be indistinguishable from the duplicate skip
        # above. A duplicate loses NOTHING (the row is already there); any
        # other error loses THIS event, and swallowing it whole left the feed
        # dead while `poll_events` kept answering `[]` — the exact silent
        # deletion §17.1's third door must not produce. So the two are told
        # apart by what the store actually holds, and only a real loss is
        # recorded as the named degraded state.
        verified = _event_row_exists(proj, event_id)
        if verified is True:
            # The unique constraint fired on a retried `event_id`: the row we
            # would have written is already in the store, so nothing is lost.
            logger.warning(
                "event_store: duplicate event_id %r skipped", event_id)
            return False
        if verified is None:
            # The row write failed AND the read-back failed: neither "lost" nor
            # "landed" is provable, so the state names the UNCERTAINTY rather
            # than asserting a loss nobody can confirm.
            state = record_degraded(proj, "append-unverified", type_,
                                    event_id, exc)
            logger.warning(
                "event_store: append failed for event_id %r and the store could "
                "not be read back — the change feed is DEGRADED (%s "
                "unverified, %s: %s); poll_events will refuse to answer until "
                "this is acknowledged (#9208)",
                event_id, state["lost"], type(exc).__name__, exc)
            return False
        state = record_degraded(proj, "append", type_, event_id, exc)
        logger.warning(
            "event_store: append failed for event_id %r — the change feed is "
            "DEGRADED (%s lost, %s: %s); poll_events will refuse to answer "
            "until this is acknowledged (#9208)",
            event_id, state["lost"], type(exc).__name__, exc)
        return False


def _event_row_exists(proj, event_id: str) -> bool | None:
    """Does the store already hold this `event_id`? (#9208's loss discriminator)

    TRI-state on purpose. ``True`` = the row is there, so a failed write lost
    nothing (the unique-constraint race). ``False`` = the store was read and does
    NOT hold it, so this event is provably lost. ``None`` = the read itself
    failed, so neither claim is available.

    Collapsing ``None`` into ``False`` would report "lost" for a transient read
    error on a store that may have committed — a permanent refusal over an
    unproven loss. Collapsing it into ``True`` is the opposite lie. The caller
    names the third outcome instead.
    """
    try:
        rows = proj.g.query(
            "MATCH (e:GraphEvent {event_id:$id}) RETURN count(e)",
            params={"id": event_id},
        ).result_set
    except Exception:  # noqa: BLE001, RUF100
        return None
    return bool(rows and rows[0][0] > 0)


#: The page bound `read_after` enforces. It is the SAME domain `GET /v1/events`
#: already declares — `Query(100, ge=1, le=MAX_PAGE)` (`hosted_api.events_poll`)
#: — and the one `read_after`'s own docstring states. It was declared in
#: two places (#8447 row #6) and silently clamped in two others: this seam
#: (`max(1, min(int(limit), 1000))`, the defect) and the `webhook_delivery`
#: page read, which carried its own copy of the literal.
#:
#: OVERRIDES: AIP-158's coerce-down for a `page_size` above the declared maximum
#: (the Google-approved default — an over-max ask is coerced DOWN to the
#: maximum, and only a NEGATIVE one is an INVALID_ARGUMENT). Coercing here
#: would be the silent truncation #8447 forbids: `read_after` returns a bare
#: `list[dict]` and `poll_events`' `next_cursor` is never empty, so a coerced
#: page is indistinguishable from a drained stream — which is exactly why
#: AIP-158 permits coerce-down only alongside an empty `next_page_token` that
#: marks end-of-collection. An out-of-domain ask is REFUSED instead. The
#: ruling is recorded on #8447.
MAX_PAGE = 1000


def read_after(proj, after_seq: int, types: list[str] | None = None,
               limit: int = 100) -> list[dict]:
    """Return events with seq > after_seq, ordered seq ASC, deduped by event_id.

    `types` filters by event type if provided. `limit` caps the page and must
    be an integer in ``1 .. MAX_PAGE`` (default 100).

    ⚠️ ``len(page) < limit`` is NOT evidence that the stream is drained — page
    by ``seq``, passing the last ``seq`` you received as ``after_seq``. Two
    reasons, and only the first is #8447's: an out-of-domain ask is REFUSED
    (see below), and a duplicate ``event_id`` is dropped AFTER the query's
    ``LIMIT`` (defense in depth), so a page of ``limit`` rows can legitimately
    come back with fewer distinct rows while more remain beyond it (pinned by
    ``test_a_duplicate_event_id_inside_a_full_page_shrinks_it``).

    #8447 row #6: an ask OUTSIDE that domain is REFUSED (``ValueError``) rather
    than clamped. It used to be ``max(1, min(int(limit), 1000))`` — so a caller
    asking for 5000 got 1000 rows and was told nothing, and because
    ``len(page) < limit`` the short page read as *the stream is drained* while
    4000 events were still undelivered (the contract #8447 pins: a bounded
    operation returns complete results or says it is incomplete, never a
    silently truncated page indistinguishable from a complete one). Refusing
    at the reference point — rather than capping the caller's ask — is the same
    instrument ``next_seq`` uses for an out-of-domain ``seq`` (#5379). It also
    departs from AIP-158's coerce-down default; see the ``OVERRIDES:`` line
    above ``MAX_PAGE`` for why that departure is deliberate.

    Dedup: a duplicate event_id (defense in depth — the unique constraint
    normally prevents them) keeps the FIRST occurrence.
    """
    raw = limit
    try:
        limit = int(raw)
    except (TypeError, ValueError, OverflowError):
        # `int()` alone leaves the seam's own refusal un-named: `inf` used to
        # escape as OverflowError, which a caller catching ValueError (the
        # documented answer) would miss.
        raise ValueError(f"limit must be a whole number, got {raw!r}") from None
    if limit != raw:
        # A VALUE comparison, not an isinstance check: `int()` also floors
        # `Decimal('2.9')` and `np.float32(2.9)`, which are not `float`
        # instances — the silent shrink this refuses. `"5000"` is refused too:
        # reporting the converted 5000 would describe a call that never
        # happened (the same rule as the range message below).
        raise ValueError(f"limit must be a whole number, got {raw!r}")
    if not 1 <= limit <= MAX_PAGE:
        raise ValueError(
            f"limit must be between 1 and {MAX_PAGE}, got {limit}. "
            "An out-of-domain page size is REFUSED rather than clamped: a "
            "silently clamped page is indistinguishable from a complete one, "
            f"so the caller cannot tell {MAX_PAGE} rows from \"the stream ends "
            "here\". Ask for a page within the bound and continue from the "
            "last seq you received (#8447).")
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
    from .projection import _graph_metadata_write_scope
    # §17.1 (#9124): RETENTION is graph metadata — the rows are the store's own
    # bookkeeping, deleted by policy, and a rebuild recreates nothing here because
    # it never replays them (#4664).
    with _graph_metadata_write_scope("event-store-purge"):
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
    from .projection import _graph_metadata_write_scope
    # §17.1 (#9124): the size-cap purge is the same graph-metadata lane as
    # `purge_expired` above (retention by policy, never replayed by a rebuild).
    with _graph_metadata_write_scope("event-store-purge"):
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
    from .projection import _graph_metadata_write_scope
    try:
        # §17.1 (#9124): the purge FLOOR is the same graph-metadata lane as the
        # purge that triggers it — it describes the store, not the corpus.
        with _graph_metadata_write_scope("event-store-purge"):
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
