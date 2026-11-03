"""In-memory degraded-fallback corpus snapshot (#1375).

The degraded path (all retrieval strategies failed → in-memory TF-IDF) used to
re-fetch the whole corpus with FULL payloads via ``self.query`` (~350ms-2s on
Docker for ~1000 points) and re-encode/re-fit per call (8-700ms variable).
This module keeps a LEAN projection (id/content/pointKind/status) plus cached
document vectors, invalidated by a dirty flag on the write surfaces
(``_mark_dirty`` hook — one hook covers create/update/supersede/retract/
operator/mitigation/delete/ingest/dream) with a LAZY TTL backstop (age check
at read — zero steady-state cost; logs when it fires while clean, signalling
a write bypassed the normal surfaces).

Vector parity: when an embedding model is present, doc vectors come from
``model.encode(texts)`` (cached) and the query is encoded per call — same
model, same texts, so rankings match the legacy neural path (the batch-vs-
separate encode caveat is documented; the sklearn path is byte-identical to
legacy per the #399 fit-on-docs/transform-query contract and is what the
parity test verifies).

Mirrors the retrieval layer's exclusions: non-operators, retracted and
``outdated=true`` points are excluded at build; terminal-status points
(retracted/superseded/outdated/archived/deprecated) are filtered at serve
unless ``include_terminal`` (the #1391 contract); ``kind`` and
``exclude_status`` compose on top.

Size cap: above MAX_CORPUS_POINTS the snapshot is skipped (hosted OOM
protection) and the legacy path runs unchanged.
"""
from __future__ import annotations

import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

MAX_CORPUS_POINTS = 50_000
SNAPSHOT_TTL_SECONDS = 60.0

# Bound the process-global cache. With per-backend keys the store is no longer
# bounded by the number of (graph_name, namespace) pairs a process holds, so a
# long-lived process that opens many distinct embedded DBs (or :memory: stores)
# would otherwise retain one corpus each (#7760 review). LRU: a fresh ``get``
# bumps an entry, so a hot store survives — the hosted per-request-SDK pattern
# must keep its one graph's snapshot across requests.
MAX_SNAPSHOT_ENTRIES = 8

# Holds ALL non-operator points (incl. terminal statuses + outdated flag);
# the serve-time exclusion in search_snapshot mirrors self.query's two-mode
# semantics (#1391: terminal statuses + outdated excluded unless include_terminal).
_SNAPSHOT_QUERY = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
    "RETURN n.id, n.content, n.pointKind, n.status, "
    "       coalesce(n.outdated, false), coalesce(n.search_keys, ''), "
    "       coalesce(n.has_answer, false)"
)


class FallbackSnapshotStore:
    """Thread-safe LRU snapshot store keyed by ``snapshot_key``.

    Bounded by ``MAX_SNAPSHOT_ENTRIES`` — the per-backend key is not bounded by
    the (graph_name, namespace) pair count the way the old key was (#7760
    review).
    """

    def __init__(self) -> None:
        self._store: dict[tuple, dict] = {}
        self._lock = threading.RLock()
        #: Whole-store EPOCH, bumped only by ``clear()`` (a reset, not a write).
        #: A build captures it with the key's own stamp and re-checks both before
        #: caching the result, so a write that lands MID-BUILD can never be undone
        #: by that build's ``put`` (#10602 review round 2).
        #:
        #: ⛔ PER-KEY, NOT ONE GLOBAL COUNTER (review round 4, P1). The first
        #: version compared a SINGLE process-global integer, and ``invalidate()``
        #: bumped it for every key — so a write to ANY other graph refused the
        #: cache insert of every build overlapping it. Measured: 20 sequential
        #: reads of key A with ONE ``invalidate(keyB)`` inside each build window →
        #: **20 builds, 0 cache hits**, against 19/20 with no foreign write. In a
        #: writing fleet — #10602's own scenario — that turns a warm cache into a
        #: rebuild-per-read on the one path that only runs when the store is already
        #: under strain, i.e. WORSE than the pre-change unconditional ``put``.
        #: `_store` is a module-level singleton shared by every tenant in a hosted
        #: process, so the blast radius of the global counter was the whole box.
        self._generation = 0
        #: key -> write counter, bumped by ``invalidate(key)`` for THAT key only.
        #: Deliberately NOT pruned yet, and the reason is stated honestly because the
        #: first version of this comment got it WRONG (review round 7, P3): it claimed
        #: pruning "needs ``_FLIGHTS_GUARD`` while ``self._lock`` is held — the inverse
        #: of the order ``get_or_build`` takes, i.e. a deadlock". That order does not
        #: exist: the two locks are NEVER nested anywhere (``_flight_begin``/
        #: ``_flight_end`` touch only ``_FLIGHTS_GUARD``; every store-lock acquisition
        #: in ``get_or_build`` sits outside those guard blocks). Pruning under
        #: ``self._lock`` → ``_FLIGHTS_GUARD`` would therefore be safe.
        #:
        #: The real trade: growth is MONOTONIC and UNCAPPED in the number of distinct
        #: (graph, namespace) keys this process has ever invalidated (~200 bytes each —
        #: a tuple plus an int), NOT bounded by ``MAX_SNAPSHOT_ENTRIES``; ``clear()`` is
        #: the only thing that empties it and has no production call site. That is
        #: accepted here because the per-entry cost is 5 orders of magnitude below the
        #: snapshot bodies it protects (50k-row corpora), and pruning it would be new
        #: concurrency surface on a P0 read path for no measured gain. It IS a real
        #: follow-up for a long-lived process serving very many graphs (bound the dict
        #: to keys present in ``_store`` or in flight, which the never-nested lock order
        #: permits). Do not read this as "negligible by construction".
        self._key_generation: dict[tuple, int] = {}

    def generation(self, key: tuple) -> tuple[int, int]:
        """Invalidation stamp for ``key`` (see ``get_or_build``'s STALENESS note).

        A ``(epoch, key_stamp)`` pair: the epoch moves on ``clear()``, the stamp only
        on ``invalidate(key)`` for THIS key. A write to another graph must not refuse
        this key's cache insert (review round 4, P1).
        """
        with self._lock:
            return (self._generation, self._key_generation.get(key, 0))

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._key_generation[key] = self._key_generation.get(key, 0) + 1
            self._store.pop(key, None)

    def get(self, key: tuple) -> dict | None:
        """Fresh snapshot or None (missing / dirty / TTL-expired)."""
        now = time.monotonic()
        with self._lock:
            snap = self._store.get(key)
            if snap is None:
                return None
            if snap.get("dirty"):
                del self._store[key]
                return None
            if now - snap["built_at"] > SNAPSHOT_TTL_SECONDS:
                logger.warning(
                    "Fallback snapshot %r TTL-fired while clean — a write may "
                    "have bypassed the normal write surfaces", key,
                )
                del self._store[key]
                return None
            self._store.pop(key, None)  # LRU bump — keep a hot store
            self._store[key] = snap
            return snap

    def put(self, key: tuple, snap: dict) -> None:
        """Insert unconditionally — TEST-ONLY since #10602 (review P2).

        No production caller uses this any more: the read path goes through
        ``put_if_generation``, which refuses a snapshot a write has overtaken.
        Left public because the store's own tests seed and evict through it, but
        an UNGUARDED insert beside the guarded one is exactly the stale-put bug
        the generation guard exists to stop, so a new production caller should
        reach for ``put_if_generation`` instead.
        """
        with self._lock:
            self._put_locked(key, snap)

    def _put_locked(self, key: tuple, snap: dict) -> None:
        """Insert under the (already held) lock, evicting the LRU tail."""
        self._store.pop(key, None)  # re-insert so order is by recency
        self._store[key] = snap
        while len(self._store) > MAX_SNAPSHOT_ENTRIES:
            self._store.pop(next(iter(self._store)), None)

    def put_if_generation(self, key: tuple, snap: dict, generation: tuple[int, int]) -> bool:
        """Insert ``snap`` only if THIS key was not invalidated since ``generation``.

        The generation COMPARE and the insert happen inside the SAME lock hold, so a
        write whose ``invalidate()`` lands between the two cannot be silently undone
        by this ``put`` (review round 2: the compare and the insert were separate
        critical sections, which left exactly that window). The comparison is
        PER-KEY (review round 4, P1): a write to another graph must not refuse this
        insert. Returns whether it was cached.
        """
        with self._lock:
            if (self._generation, self._key_generation.get(key, 0)) != generation:
                return False
            self._put_locked(key, snap)
            return True

    def clear(self) -> None:
        with self._lock:
            self._generation += 1
            self._key_generation.clear()
            self._store.clear()


_store = FallbackSnapshotStore()

# ── #10602: SINGLE-FLIGHT build (one whole-corpus fetch per key, not one per reader) ──
#
# The degraded read used to go ``get(key)`` → miss → ``build_snapshot(proj)`` → ``put``
# with NOTHING serialising the build. Every concurrent reader therefore ran its own
# full-corpus fetch: measured on an idle local store holding 10,000 points, N
# simultaneous degraded reads cost N builds (build p50 1,441 ms cold / 388 ms warm),
# so 8 readers all took **3.7–4.6 s each** (wall 4,572 ms) while ONE reader took 595 ms.
# Latency therefore grew linearly with the number of agents searching — the exact
# shape #10602 records (5 concurrent agents, every call 13,204 / 13,201 / 13,307 /
# 13,302 / 13,306 ms — tightly clustered, because they all wait on the same corpus),
# and it is self-amplifying: the busier the fleet, the more whole-corpus reads the
# store is asked for *while it is already the thing under strain*.
#
# What this is NOT: it changes no cache policy, no TTL, no size cap, no bound and no
# result. Nothing is cached that was not already cached, and no answer changes.
#
# ⛔ It is also not the "add a cache" remedy #10602's recorded decisions forbid
# (STORAGE-ARCHITECTURE §14.2 / §12.1c make FalkorDB itself the cache under the
# hybrid): the entry, its lifetime and its write-side invalidation are unchanged —
# this only stops N readers doing identical work at once.
#
# WHY A FLIGHT AND NOT A PER-KEY LOCK (review rounds 1-2). A bare lock serialises the
# FAILURE outcome too: ``build_snapshot`` returns ``None`` above ``MAX_CORPUS_POINTS``
# and on any query exception, and a ``None`` is never cached — so N readers took the
# lock one after another and each re-ran the identical doomed build, i.e. exactly the
# concurrency-linear latency this fixes, on the two paths it most needed to help
# (an over-cap hosted graph, and a store whose snapshot query is failing). A flight
# publishes ONE outcome — success or ``None`` — to every reader that joined it, so the
# failure path costs one BUILD ATTEMPT, not N (narrowed, review P2: a published ``None``
# still sends every reader down the legacy path, whose full-payload fetch is per reader —
# that remaining amplification is #10602's residual, not something this flight claims to
# close). A lock registry also had to be evicted to stay
# bounded, and an evicted-then-re-minted lock let two builds run at once for one key
# (the bug, back). A flight is removed when its last waiter leaves, so the registry is
# bounded by what is IN FLIGHT and needs no eviction at all.
#
# KEYED BY ``(store, key)``, not by ``key`` alone (review P2): ``get_or_build`` accepts a
# ``store=`` seam, and a registry keyed by the snapshot key alone would coalesce two
# distinct stores onto ONE builder — the joiner then receives the OTHER store's snapshot
# for that key, a silent wrong answer rather than a slow one.
_FLIGHTS: dict[tuple, _Flight] = {}
_FLIGHTS_GUARD = threading.Lock()


class _Flight:
    """One in-flight corpus build, shared by everyone waiting on the same key."""

    __slots__ = ("done", "generation", "result", "waiters")

    def __init__(self) -> None:
        self.done = threading.Event()
        #: Only read once ``done`` is set. ``None`` means the build produced nothing
        #: and the legacy path runs — one of its three documented causes: too big
        #: (``MAX_CORPUS_POINTS``), its query failed, or it RAISED (review round 4,
        #: P3: an uncaught raise used to reach the leader only, while every joiner
        #: read this ``None`` — one failure, two different answers).
        self.result: dict | None = None
        #: The key's invalidation stamp captured BEFORE the build. ``None`` when the
        #: leader was served from a warm entry (no build ran). A joiner compares it
        #: against the store's current stamp to tell "I arrived before the write"
        #: from "I arrived AFTER it" — see the joiner branch in ``get_or_build``.
        self.generation: tuple[int, int] | None = None
        self.waiters = 0


def _flight_begin(store: FallbackSnapshotStore, key: tuple) -> tuple[_Flight, bool]:
    """Join (or open) the flight for ``(store, key)``. Returns ``(flight, is_leader)``."""
    flight_key = (store, key)
    with _FLIGHTS_GUARD:
        flight = _FLIGHTS.get(flight_key)
        if flight is not None:
            flight.waiters += 1
            return flight, False
        flight = _Flight()
        flight.waiters = 1
        _FLIGHTS[flight_key] = flight
        return flight, True


def _flight_end(
    store: FallbackSnapshotStore, key: tuple, flight: _Flight
) -> None:
    """Leave a flight; the last one out removes it (registry is in-flight-bounded)."""
    flight_key = (store, key)
    with _FLIGHTS_GUARD:
        flight.waiters -= 1
        if flight.waiters <= 0 and _FLIGHTS.get(flight_key) is flight:
            del _FLIGHTS[flight_key]


def get_or_build(proj, key: tuple, *, store: FallbackSnapshotStore | None = None,
                 builder=None) -> dict | None:
    """Cached snapshot for ``key``, building it at most ONCE per key at a time.

    Behaviourally identical to the read path's ``get``/``build_snapshot``/``put``
    sequence — same entry, same ``None``-on-too-big / ``None``-on-failed contract, same
    write-side invalidation — except that concurrent readers COALESCE onto one build.

    STALENESS, stated exactly (review round 2 corrected an overstatement here). A build
    can be OVERTAKEN by a write: rows are read at t1, ``_mark_dirty`` →
    ``invalidate(key)`` lands at t2, and a naive ``put`` would publish the pre-write rows
    for the full 60 s TTL — to every reader, where before each reader built its own and
    a later one self-healed over the stale entry. So the store's generation is captured
    before the build and re-checked before the ``put``: if any write invalidated while
    the build ran, the result is returned to that build's cohort but NOT cached, and the
    next reader starts a fresh build. A cohort's own staleness is then bounded by the
    build it joined — the very rows it would have read itself — never by the TTL.

    ``builder``/``store`` are test seams.

    ⛔ PRECONDITION: ``builder`` must NOT call ``get_or_build`` for the same ``key``.
    A nested call would join the caller's own flight and block forever in
    ``flight.done.wait()`` — which is deliberately unbounded — permanently losing
    the worker thread with no log (review round 7, P3: measured, not theoretical).
    Unreachable today: the only builder is ``build_snapshot``, which makes no such
    call. This line exists so the next reshape that routes a builder through this
    seam fails loudly instead of wedging a flight.
    """
    store = _store if store is None else store
    build = build_snapshot if builder is None else builder

    #: A late joiner may re-enter the flight ONCE (see the staleness branch below);
    #: bounded on purpose, so a stream of writes cannot livelock a reader. The
    #: bounded fallback is the pre-#10602 behaviour — this reader builds for itself
    #: — never a stale answer and never a spin.
    reentered = False
    while True:
        snap = store.get(key)
        if snap is not None:
            return snap

        flight, leader = _flight_begin(store, key)
        if leader:
            try:
                # Re-check under leadership: a build may have completed between the
                # miss above and this flight opening.
                snap = store.get(key)
                if snap is None:
                    generation = store.generation(key)
                    flight.generation = generation
                    try:
                        snap = build(proj)
                    except Exception:  # noqa: BLE001, RUF100
                        # A raise here is NOT one of the two failures ``build_snapshot``
                        # swallows itself (over-cap, query exception) — it is an
                        # unexpected error. Uncaught, it would propagate to the LEADER
                        # while every joiner read ``result is None`` and quietly took the
                        # legacy path: one failure, two different answers (review round
                        # 4, P3). Publish the documented None contract to the whole
                        # cohort instead, so every reader of this key takes the same
                        # path — and LOG it, because a silent swallow would hide a bug.
                        logger.exception(
                            "#10602: corpus build raised — serving this key's cohort "
                            "the legacy path (key=%r)", key)
                        snap = None
                    # Publish only if no write invalidated the corpus mid-build (see
                    # STALENESS above) — otherwise this cohort keeps its answer and the
                    # next reader rebuilds, which is the self-healing the pre-#10602
                    # per-reader build got for free. The compare and the insert are ONE
                    # hold of the store lock, so a write landing between them cannot be
                    # undone by this put (review round 2).
                    if snap is not None:
                        store.put_if_generation(key, snap, generation)
                flight.result = snap
            finally:
                flight.done.set()
                _flight_end(store, key, flight)
            return flight.result

        try:
            # NOT deadline-bounded, and the comment says so on purpose (review round 2):
            # the two corpus queries are bounded by the store socket timeouts (connect
            # 2 s / read 10 s), but ``build_snapshot`` then runs ``EmbeddingModel.encode``
            # (a hosted provider call) and ``TfidfVectorizer.fit`` over up to
            # ``MAX_CORPUS_POINTS`` documents, and NEITHER carries a deadline. So a stuck
            # embedder pins every reader of this key — the same hang those readers each
            # hit individually before #10602, now shared. Do not read this wait as bounded.
            flight.done.wait()
            stale = (
                flight.result is not None
                and flight.generation is not None
                and store.generation(key) != flight.generation
            )
            if not stale:
                return flight.result
        finally:
            _flight_end(store, key, flight)

        # A write landed while the build this reader joined ran, and THIS reader
        # arrived AFTER it — its own ``store.get`` above missed, so serving the
        # leader's rows would hand the caller pre-write data after its own write
        # committed (review round 6, P2: the generation guard stopped that result
        # being CACHED, but not being DELIVERED to a late joiner; pre-#10602 this
        # reader ran its own build and saw the write).
        #
        # ⛔ NOT ``return get_or_build(...)``: the flight is still registered until
        # the leader's ``_flight_end``, so a recursive call would re-join this
        # completed flight and spin. The flight is ENDED above (the ``finally``)
        # before anything else happens, which is what makes re-entry safe.
        #
        # The first clean rebuild after the write IS cached (``put_if_generation``
        # with a matching generation), so check the store first: a cohort of N late
        # joiners then pays for ONE build, not N (review round 8, P1: building inline
        # here ran a whole-corpus build PER WAITER — the very amplification #10602
        # exists to remove, on the path its own comment calls "a writing fleet").
        generation = store.generation(key)
        fresh = store.get(key)
        if fresh is not None:
            return fresh
        if not reentered:
            # Re-enter as a NEW leader: the rebuild coalesces onto a fresh flight
            # instead of running once per waiter. BOUNDED to one re-entry (see
            # ``reentered`` above).
            reentered = True
            continue
        try:
            fresh = build(proj)
        except Exception:  # noqa: BLE001, RUF100
            logger.exception(
                "#10602: rebuild for a late joiner raised — serving the legacy "
                "path (key=%r)", key)
            return None
        if fresh is not None:
            store.put_if_generation(key, fresh, generation)
        return fresh


# Stable key for a ``:memory:`` projection whose client exposes no store
# identity handle at all. All such projections share it, which buys
# cacheability at the cost of per-store ISOLATION — not in one direction only:
# this same tuple also keys the READ path (``get_or_build(proj, _key, …)``), so the
# first such store to build caches its snapshot under it and a DIFFERENT store
# is then served that snapshot, with no write required. That is acceptable only
# because the branch is unreachable for a real client: a real embedded client
# always exposes ``socket_file`` (on ``db`` or ``db.client``), and only a
# client exposing no handle at all can land here — where two stores are
# indistinguishable, so the real choice is a shared cacheable key versus a
# per-call key that can never be hit. Reachability is nil today; if a real
# client ever stops exposing a handle, this key must be revisited.
_UNIDENTIFIED_MEMORY_STORE = "<unidentified-memory-store>"


def _embedded_store_identity(proj) -> str:
    """Identity of the embedded store a ``:memory:`` projection uses.

    redislite keys its running-daemon registry by ``<cwd>/:memory:.settings``,
    so every ``FalkorProjection`` over ``":memory:"`` in one CWD attaches to the SAME
    daemon and the SAME graph — the second sees the first's writes. The corpus
    identity must therefore be the STORE INSTANCE, not the projection: a
    per-projection token gives two projections over one store two keys, so
    ``_mark_dirty``'s ``invalidate`` reaches only the writer's key and a
    sibling's cached snapshot survives the write — a just-written point stays
    invisible and a just-deleted point is still served until the TTL (#7773
    P1).

    The registry path alone is only the SLOT, not the instance: a daemon that
    replaces a dead one at the same ``<cwd>/:memory:.settings`` starts a fresh
    (empty) graph, so a key on the slot matches the dead store's cached
    snapshot and serves phantom points (#7773 review). The live daemon's
    ``socket_file`` is preferred: every projection attached to one daemon
    shares it, and a replacement daemon gets a new temp socket, so the identity
    distinguishes instances while still unifying siblings. The registry path is
    the fallback for a client that exposes only that.

    When neither handle exposes a value, the key is a process-wide constant —
    stable, shared by every such projection, and logged LOUDLY — rather than
    re-minted per call. Sharing is NOT isolation-preserving: this tuple also
    keys the read path, so one such store's snapshot is served to another. It
    is safe only because the branch is unreachable for a real client — both a
    real ``FalkorProjection`` and redislite expose ``socket_file``, so no real
    store reaches here. The old code instead stamped a per-projection token and
    suppressed the failed assignment, so a client that REJECTS the stamp
    (``__slots__``/immutable) got a fresh key on every call: the entry could
    never be cache-hit and the corpus re-fetch + TF-IDF re-fit ran silently on
    every degraded search (#7773 P2).
    """
    db = getattr(proj, "db", None)
    handles = (db, getattr(db, "client", None))
    for attr in ("socket_file", "settingregistryfile"):
        for handle in handles:
            val = getattr(handle, attr, None)
            if isinstance(val, str) and val:
                return os.path.realpath(val)
    logger.error(
        "Fallback snapshot: the :memory: store identity is unavailable from "
        "the projection's client (no socket_file/settingregistryfile) — using "
        "the process-wide unidentified-store key. That key is cacheable but "
        "NOT isolated: every such store shares one snapshot slot, so one "
        "store's snapshot can be served to another on a read. A real client "
        "always exposes a socket_file, so this branch should be unreachable "
        "(#7773 P2).",
    )
    return _UNIDENTIFIED_MEMORY_STORE


def snapshot_key(proj, namespace: str | None) -> tuple:
    """Identity of the corpus a snapshot describes.

    ``graph_name`` alone does NOT identify an embedded store: every embedded DB
    defaults to ``'tortoise'``, so a store keyed on it alone served one DB's
    snapshot to another — the leaked point in #7760 (a test's degraded search
    returned a point written by a different test's SDK on a different file).
    The embedded file's realpath is the second half of the identity — the same
    reason #3049's ``_prewipe_graph_identity`` carries ``db_path``.

    A server/URI graph carries no file, so its identity IS its graph name
    (already the first element), matching #3049; a process holding two servers
    that carry the SAME graph name is out of contract here and still shares a
    slot (pre-existing, and unchanged by this key). ``:memory:`` carries no
    file either, but it is NOT a private store per projection: redislite
    resolves it to a shared embedded daemon whose identity is its live socket
    (``_embedded_store_identity``).
    """
    path = getattr(proj, "_path", None)
    if path == ":memory:":
        backend = ("memory", _embedded_store_identity(proj))
    else:
        from tortoise.projection import _prewipe_db_path_identity
        backend = _prewipe_db_path_identity(path)
    return (getattr(proj, "graph_name", "tortoise"), backend, namespace)


def build_snapshot(proj) -> dict | None:
    """Lean corpus projection + cached document vectors. None if too big.

    ⛔ Call this through ``get_or_build`` from anything on a request path: a bare
    call is a whole-corpus fetch, and N concurrent callers are N fetches (#10602).
    """
    g = proj.g
    try:
        count = g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
            "RETURN count(n)",
        ).result_set[0][0]
        if int(count) > MAX_CORPUS_POINTS:
            logger.info(
                "Fallback snapshot skipped — corpus %d > cap %d",
                int(count), MAX_CORPUS_POINTS,
            )
            return None
        rows = g.query(_SNAPSHOT_QUERY).result_set
        points = [
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or "",
             "status": r[3] or "", "outdated": bool(r[4]),
             # R2 (#1541) D4: search_keys joins the snapshot (flat string in
             # the graph since R2; legacy lists handled by index_text).
             "search_keys": r[5] or "",
             # A5 (#2070): stored evidence mark rides the snapshot hits —
             # wired through the degraded lane (snapshot graph carries the
             # mark when the extractor wrote it; product graphs: zero marks).
             "has_answer": bool(r[6])}
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("Fallback snapshot build query failed: %s", e)
        return None

    vectorizer = doc_vecs = None
    model_id = None
    # R2 (#1541) D4: the corpus text is content ∪ search_keys — the
    # embedded-stack counterpart of the real backend's multi-field Point
    # FTS index (surface-11 normalization is the shared index_text builder).
    from tortoise.sparse import index_text
    texts = [index_text(p["content"], p["search_keys"]) for p in points]
    try:
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            doc_vecs = np.asarray(
                model.encode(texts, show_progress_bar=False), dtype=np.float64,
            )
            model_id = getattr(model, "model_id", "embedding-model")
    except Exception as e:  # noqa: BLE001, RUF100
        logger.info("Fallback snapshot model encoding unavailable: %s", e)

    if doc_vecs is None:
        try:
            from tortoise.heavy_imports import import_tfidf_vectorizer  # #5718
            TfidfVectorizer = import_tfidf_vectorizer()
            tv = TfidfVectorizer()
            # Keep the sparse matrix — densify only the served slice (P2: the
            # dense 50k × vocab array is an OOM risk; csr stays lean).
            doc_vecs = tv.fit_transform(texts)
            vectorizer = tv
        except Exception as e:  # noqa: BLE001, RUF100
            logger.info("Fallback snapshot vectorization unavailable (sklearn): %s", e)

    return {
        "built_at": time.monotonic(),
        "dirty": False,
        "points": points,
        "vectorizer": vectorizer,
        "doc_vecs": doc_vecs,
        "model_id": model_id,
    }


def _encode_query(query: str, snap: dict):
    """Encode the query the way the corpus was encoded (model or sklearn)."""
    if snap.get("model_id"):
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            return np.asarray(model.encode([query], show_progress_bar=False),
                              dtype=np.float64)[0]
        return None
    if snap.get("vectorizer") is not None:
        import numpy as np
        return np.asarray(snap["vectorizer"].transform([query]).toarray()[0],
                          dtype=np.float64)
    return None


def search_snapshot(
    query: str,
    snap: dict,
    *,
    limit: int = 10,
    kind: str | None = None,
    exclude_status: list[str] | None = None,
    exclude_turn_echo_session: str | None = None,
    include_terminal: bool = False,
    threshold: float = 0.0,
) -> list[dict]:
    """Score the snapshot corpus against ``query``.

    Returns the SAME shape as ``search_engine.fallback_tfidf`` (a list of
    SearchResult.to_dict() with match_source="tfidf"). Mirrors the legacy
    fallback semantics: ``kind`` (pointKind equality), terminal-status
    exclusion unless ``include_terminal`` (#1391), and ``exclude_status``
    compose. ``exclude_turn_echo_session`` (#4509) drops that session's own
    turn echoes from the CORPUS before ranking — the same pre-truncation
    contract the primary path applies, so the degraded tier cannot leak a
    capture's transcript as memory priors. When no cached vectors exist,
    delegates to the legacy scorer.
    """
    import numpy as np  # noqa: I001

    from tortoise.embeddings import search_points
    from tortoise.search_engine import (
        SearchResult, SearchScores, TERMINAL_EXCLUDED_STATUSES,
    )

    points = snap["points"]
    mask = None

    def _filter(pred) -> None:
        nonlocal points, mask
        idx = [i for i, p in enumerate(points) if pred(p)]
        points = [points[i] for i in idx]
        mask = idx if mask is None else [mask[i] for i in idx]

    if kind:
        _filter(lambda p: p["pointKind"] == kind)
    if not include_terminal:
        # #1391 two-mode semantics: terminal statuses AND the legacy outdated
        # flag are excluded unless include_terminal surfaces them.
        _term = set(TERMINAL_EXCLUDED_STATUSES)
        _filter(lambda p: p["status"] not in _term and not p.get("outdated"))
    if exclude_status:
        _ex = set(exclude_status)
        _filter(lambda p: p["status"] not in _ex)
    if exclude_turn_echo_session:
        # #4509: pre-RANKING/pre-truncation, mirroring the primary path's seam.
        # Lazy import keeps this stdlib-light leaf's import graph unchanged on
        # the default path (the branch is not entered when not opted in).
        from tortoise.retrieval import is_turn_echo_row
        _echo_sid = exclude_turn_echo_session
        _filter(lambda p: not is_turn_echo_row(
            _echo_sid,
            {"id": p.get("id"), "point_kind": p.get("pointKind"),
             "content": p.get("content")}))
    if not points:
        return []

    try:
        query_vec = _encode_query(query, snap)
    except Exception:  # noqa: BLE001, RUF100
        query_vec = None
    scored: list[dict] = []
    vectors_failed = False
    if query_vec is not None and snap["doc_vecs"] is not None:
        try:
            doc_vecs = snap["doc_vecs"]
            if mask is not None:
                doc_vecs = doc_vecs[mask] if hasattr(doc_vecs, "__getitem__") else doc_vecs
            # csr slice stays sparse; densify only what we serve
            if hasattr(doc_vecs, "toarray"):
                dense = np.asarray(doc_vecs.toarray(), dtype=np.float64)
            else:
                dense = np.asarray(doc_vecs, dtype=np.float64)
            # true cosine (query normalized too — P2 parity of similarity values)
            q_norm = np.linalg.norm(query_vec) or 1.0
            norms = np.linalg.norm(dense, axis=1, keepdims=True)
            norms[norms == 0] = 1
            sims = (dense @ query_vec) / (norms[:, 0] * q_norm)
            order = np.argsort(-sims)
            scored = [
                {"id": points[i]["id"], "content": points[i]["content"],
                 "similarity": float(sims[i])}
                for i in order
                if sims[i] >= threshold
            ][:limit]
        except Exception:  # noqa: BLE001, RUF100
            vectors_failed = True
    if (not scored and query_vec is None) or vectors_failed:
        # Cached vectors unavailable/failed — legacy in-memory scorer (same inputs).
        # R2 (#1541) D4: the scorer sees the indexed text (content ∪
        # search_keys) — same union the real stack indexes; the returned
        # payload keeps the REAL content (alias text is index-only).
        from tortoise.sparse import index_text
        real_content = {p["id"]: p["content"] for p in points}
        indexed = [
            {**p, "content": index_text(p["content"], p.get("search_keys"))}
            for p in points
        ]
        scored = search_points(query, indexed, threshold=threshold, limit=limit)
        for r in scored:
            r["content"] = real_content.get(r["id"], r["content"])

    meta = {p["id"]: p for p in points}
    return [
        SearchResult(
            id=r["id"],
            content=r["content"],
            point_kind=meta.get(r["id"], {}).get("pointKind", ""),
            scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
            match_source="tfidf",
            ep=None,
            # A5 (#2070): stored evidence mark rides the snapshot hits
            # (snapshot points carry has_answer when the graph wrote it).
            has_answer=bool(meta.get(r["id"], {}).get("has_answer")),
            # ⛔ No ``source_ref``/``captured_at`` here — the lean snapshot
            # projection excludes them by design, so
            # TORTOISE_SEARCH_PROVENANCE is a no-op on this tier; see the KNOWN
            # LIMITATION note on ``search_engine.search_provenance_enabled``.
        ).to_dict()
        for r in scored
    ]
