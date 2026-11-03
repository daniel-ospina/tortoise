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

# ── #10602: SINGLE-FLIGHT build (one whole-corpus fetch per in-flight generation) ──
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
# Which part of that is fixed, stated exactly (review round 10, P3): AT MOST ONE BUILD PER
# IN-FLIGHT GENERATION, not at most one per key. The slot is ``(key, generation)`` so a
# reader holding a NEWER stamp opens its own build while the older one is still running —
# measured: 10 concurrent readers of one key with a same-key ``invalidate`` every 50 ms and
# a 400 ms build → 9 builds, 7 of them concurrent. That is a deliberate consequence of the
# correctness rule below (a reader must never be served rows that predate a write it could
# have seen), NOT of coalescing: the pre-#10602 code ran every reader's build concurrently
# too. It is bounded by the number of distinct generations in flight, not by reader count,
# which is the property that made latency linear in the fleet. Serialising generations
# (a newer reader waits, re-checks the cache, then leads) would cap it at one build per key
# at a time and is the natural follow-up if the store's slots are the binding constraint;
# it was NOT done here because it adds cross-generation blocking to a P0 read path.
#
# What this is NOT: it adds no cache entry, no TTL and no size cap. ⛔ "It changes no cache
# POLICY" is NOT true and must not be restored (review round 13, P3: a reader taking that
# at face value concludes the freshness guard is free, which the next 25 lines disprove).
# The one admission change is the put guard below — an overtaken build is no longer cached,
# so on a graph written to faster than a build completes this LOSES cache hits the
# pre-change code had. "No answer changes" is likewise NOT true (review round 10, P2): see
# the COMMIT→INVALIDATE WINDOW note on ``get_or_build``.
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
# failure path costs one attempt, not N. A lock registry also had to be evicted to stay
# bounded, and an evicted-then-re-minted lock let two builds run at once for one key
# (the bug, back). A flight is removed when its last waiter leaves, so the registry is
# bounded by what is IN FLIGHT and needs no eviction at all.
# ⛔ KEYED BY ``(key, generation)``, NOT BY ``key`` ALONE (review round 9, P2). Keying by
# the key alone made the OVERTAKEN case fan out: every joiner of an overtaken build
# compared against the (now stale) flight generation, so the comparison was true for the
# WHOLE cohort and each of them ran its own uncoupled rebuild — measured 1 leader + 5
# joiners = **6 whole-corpus builds**, i.e. exactly the amplifier this module exists to
# remove, triggered by the ordinary event of a write landing during a build. Keying by
# the generation makes the cohort itself correct: a reader arriving after a write holds a
# different stamp and cannot join the pre-write build, so it never needs re-validating.
# The same 6-reader cohort now costs ONE build.
_FLIGHTS: dict[tuple, _Flight] = {}
_FLIGHTS_GUARD = threading.Lock()


class _Flight:
    """One in-flight corpus build, shared by everyone waiting on the same key AND generation.

    Round 13 (P3) corrected "the same key": with ``(store, key, generation)`` slotting a
    same-key reader that holds a newer generation explicitly does NOT share this flight —
    it leads its own.
    """

    __slots__ = ("done", "result", "waiters")

    def __init__(self) -> None:
        self.done = threading.Event()
        #: Only read once ``done`` is set. ``None`` means the build produced nothing
        #: and the legacy path runs — one of its three documented causes: too big
        #: (``MAX_CORPUS_POINTS``), its query failed, or it RAISED (review round 4,
        #: P3: an uncaught raise used to reach the leader only, while every joiner
        #: read this ``None`` — one failure, two different answers).
        self.result: dict | None = None
        self.waiters = 0


def _flight_slot(store, key: tuple, generation: tuple[int, int]) -> tuple:
    """Registry slot for one build: **store identity** + key + generation.

    ⛔ THE STORE IS PART OF THE SLOT (review round 13, P3). ``get_or_build`` takes
    ``store`` as a test seam, and with the slot keyed on ``(key, generation)`` alone two
    DIFFERENT stores sharing a key were handed each other's flight: a reader on store B
    joined store A's in-flight build and was served **store A's snapshot**, while
    ``b.get(key)`` stayed ``None``. Unreachable in production (the only instantiation is
    the module singleton below, and ``sdk.py`` passes exactly it), which is worse rather
    than better: a store-injection test would have been a **false PASS**. The store is
    hash-identified by its default identity hash, which is exactly the semantics wanted.
    """
    return (store, key, generation)


def _flight_begin(store, key: tuple, generation: tuple[int, int]) -> tuple[_Flight, bool]:
    """Join (or open) the flight for this store+key+generation. Returns ``(flight, leader)``."""
    slot = _flight_slot(store, key, generation)
    with _FLIGHTS_GUARD:
        flight = _FLIGHTS.get(slot)
        if flight is not None:
            flight.waiters += 1
            return flight, False
        flight = _Flight()
        flight.waiters = 1
        _FLIGHTS[slot] = flight
        return flight, True


def _flight_end(store, key: tuple, generation: tuple[int, int], flight: _Flight) -> None:
    """Leave a flight; the last one out removes it (registry is in-flight-bounded)."""
    slot = _flight_slot(store, key, generation)
    with _FLIGHTS_GUARD:
        flight.waiters -= 1
        if flight.waiters <= 0 and _FLIGHTS.get(slot) is flight:
            del _FLIGHTS[slot]


def get_or_build(proj, key: tuple, *, store: FallbackSnapshotStore | None = None,
                 builder=None) -> dict | None:
    """Cached snapshot for ``key``, building it at most once per IN-FLIGHT GENERATION.

    Same entry, same ``None``-on-too-big / ``None``-on-failed contract, same write-side
    invalidation, and concurrent readers on one generation COALESCE onto one build —
    but this is **NOT result-identical to the read path's ``get``/``build_snapshot``/
    ``put`` sequence**, and that sentence must not be restored (review round 11, P2: it
    survived here after round 10 had already forbidden the same claim 20 lines above).
    Two documented differences: a reader racing a write's commit→invalidate tail can be
    served that cohort's pre-commit rows (see the COMMIT→INVALIDATE WINDOW note), and an
    overtaken build is no longer cached, so on a graph written to faster than a build
    completes this LOSES cache hits the pre-change code had (see THE COST OF THAT
    GUARD).

    STALENESS, stated exactly (review round 2 corrected an overstatement here). A build
    can be OVERTAKEN by a write: rows are read at t1, ``_mark_dirty`` →
    ``invalidate(key)`` lands at t2, and a naive ``put`` would publish the pre-write rows
    for the full 60 s TTL — to every reader, where before each reader built its own and
    a later one self-healed over the stale entry. So the store's generation is captured
    before the build and re-checked before the ``put``: if any write invalidated while
    the build ran, the result is returned to that build's cohort but NOT cached, and the
    next reader starts a fresh build. A cohort's own staleness is then bounded by the
    build it joined — the very rows it would have read itself — never by the TTL.

    ⛔ THE COST OF THAT GUARD, stated here rather than left to be discovered (review
    round 9, P1 — this note corrects an overstated "nothing else changes" claim). On a
    graph whose write rate exceeds the inverse of the build time, EVERY build window
    contains a same-key ``invalidate`` (``_mark_dirty`` fires it for this key on every
    write surface), so nothing is ever cached and the degraded tier costs one corpus
    fetch per read. The pre-#10602 code cached that overtaken build unconditionally, so
    on such a graph IT achieved hits and this does not. That is a deliberate trade, not
    an oversight: the alternative serves rows that predate a committed write — an agent
    that has just written a memory cannot find it, for up to the 60 s TTL when writes
    stop — and the freshness half was accepted in review round 2. It is PINNED by
    ``test_same_key_writes_leave_the_degraded_cache_cold_which_is_the_stated_trade`` so
    the trade is a decision rather than an accident. The concurrency half — the part
    that made latency linear in the fleet, which is #10602's actual shape — is bounded
    by the number of distinct generations in flight rather than by reader count (see the
    BANNER note at the top of this section: at most one build per IN-FLIGHT GENERATION,
    not one per key).

    ``builder``/``store`` are test seams.

    ⛔ PRECONDITION: ``builder`` must NOT call ``get_or_build`` for the same ``key``.
    A nested call would join the caller's own flight and block forever in
    ``flight.done.wait()`` — which is deliberately unbounded — permanently losing
    the worker thread with no log (review round 7, P3: measured, not theoretical).
    Unreachable today: the only builder is ``build_snapshot``, which makes no such
    call. This line exists so the next reshape that routes a builder through this
    seam MEETS THE PRECONDITION IN ITS OWN REVIEW instead of wedging a flight — a
    docstring cannot fail loudly (review round 12, P3: the previous wording promised a
    loud failure three lines above a hazard it had just described as silent).
    """
    store = _store if store is None else store
    build = build_snapshot if builder is None else builder

    snap = store.get(key)
    if snap is not None:
        return snap

    def produce(generation: tuple[int, int]) -> dict | None:
        """Lead this generation's build: re-check the cache, build, publish.

        Never raises ON A BUILD FAILURE: an unexpected build failure is logged and
        published as ``None`` to the whole cohort, because a raise reaching only the
        leader while every joiner read ``None`` gave one failure two different
        answers (review round 4, P3).
        """
        cached = store.get(key)
        if cached is not None:
            return cached
        try:
            built = build(proj)
        except Exception:  # noqa: BLE001, RUF100
            logger.exception(
                "#10602: corpus build raised — serving this key's cohort "
                "the legacy path (key=%r)", key)
            return None
        # Publish only if no write invalidated the corpus mid-build (see
        # STALENESS above). The compare and the insert are ONE hold of the store
        # lock, so a write landing between them cannot be undone by this put
        # (review round 2); the comparison is PER-KEY (review round 4, P1).
        if built is not None:
            store.put_if_generation(key, built, generation)
        return built

    # ⛔ THE FLIGHT SLOT CARRIES THIS READER'S OWN ARRIVAL STAMP. That single choice is
    # what makes the cohort correct, and it is why no reader ever needs to rebuild:
    #
    # * A reader arriving AFTER a write has bumped the key's stamp holds a DIFFERENT
    #   stamp, so it cannot join the pre-write build at all — it leads (or joins) the
    #   build for its own generation. That is why round 6's inline per-reader rebuild is
    #   gone: the partition is structural, so nothing has to be re-validated afterwards.
    # * A reader that DOES share the leader's stamp sampled a stamp no older than the
    #   leader's, and the leader's rows were read after that stamp was taken.
    #
    # ⛔ COMMIT→INVALIDATE WINDOW — the precondition, stated instead of glossed (review
    # round 10, P2: this comment previously claimed "read-your-writes, structurally: no
    # result can cross a write boundary", which is FALSE and was measured false). The
    # stamp only moves when ``invalidate()`` runs, and ``_mark_dirty`` calls it at the
    # TAIL of each write surface, AFTER the graph commit (``sdk.py:17590`` →
    # ``:17594``, retract ``:20191``, promote ``:20346``). So there is a window between a
    # write COMMITTING and its invalidate landing. A reader whose ``store.get`` misses in
    # that window samples the PRE-write stamp and joins the pre-write flight, so it can be
    # served that cohort's pre-commit rows — bounded by one flight's duration, never by
    # the TTL. Two things are nevertheless true and are what this design actually
    # guarantees:
    #
    #   1. A WRITER's own read-your-writes holds: every write surface invalidates before
    #      it returns, so by the time the caller can issue a search the stamp has moved
    #      and the pre-write flight is unjoinable. ⛔ BEST-EFFORT, stated so it is not
    #      read as a hard guarantee: the hook swallows its own failure
    #      (``sdk.py:29896`` ``except Exception: pass``) and ``update_point``'s tag sync
    #      can abort after the promote ``SET`` landed and before ``_mark_dirty``
    #      (``sdk.py:17075``), so the stamp moves on every write that REACHES
    #      ``_mark_dirty`` — not on one whose hook raised.
    #   2. An unrelated reader racing a write's commit→invalidate tail gets the cohort's
    #      pre-commit rows, exactly as it would have got them from a cache hit; pre-#10602
    #      it built its own rows and saw the write. That is a real, narrow difference from
    #      the old behaviour, and it is the price of not having a per-reader build.
    #
    # Pinned by ``test_a_reader_racing_the_commit_invalidate_tail_gets_the_cohorts_rows``.
    #
    # ⇒ "overtaken" never means "must be REBUILT for this reader"; it only means "must
    # not be CACHED" (the put guard inside ``produce``). ⛔ NOT "never means stale for
    # this reader" — that contradicted guarantee 2 above (review round 12, P3): the
    # racing reader IS served rows predating a committed write. What the partition buys
    # is that no reader needs its own build, not that no reader sees a stale row. So
    # there is NO rebuild path here — the
    # previous version had one, keyed on "the generation changed", which was true for the
    # WHOLE cohort and cost one corpus build PER READER (review round 9, P2: measured 6
    # builds for 1 leader + 5 joiners — the amplifier this module exists to remove,
    # restored by the fix for round 6).
    generation = store.generation(key)
    flight, leader = _flight_begin(store, key, generation)
    try:
        if leader:
            flight.result = produce(generation)
        else:
            # NOT deadline-bounded, and the comment says so on purpose (review
            # round 2): the two corpus queries are bounded by the store socket
            # timeouts (connect 2 s / read 10 s), but ``build_snapshot`` then runs
            # ``EmbeddingModel.encode`` (a hosted provider call) and
            # ``TfidfVectorizer.fit`` over up to ``MAX_CORPUS_POINTS`` documents,
            # and NEITHER carries a deadline. So a stuck embedder pins every reader
            # of this key — the same hang those readers each hit individually
            # before #10602, now shared. Do not read this wait as bounded.
            flight.done.wait()
        return flight.result
    finally:
        if leader:
            flight.done.set()
        _flight_end(store, key, generation, flight)


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
