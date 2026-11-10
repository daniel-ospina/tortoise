"""#10976 — the cache EGRESS census: what MAY leave FalkorDB, and the refusal when nothing proves it.

WHY THIS EXISTS (measured 2026-11-04, private lane container)
------------------------------------------------------------
`STORAGE-ARCHITECTURE.md` §2.0 (owner, 2026-10-08) demotes FalkorDB to **a derived
hot cache** — *"Postgres is the system of record; FalkorDB is a derived hot cache"*.
It also states the cost lever is *"a chosen hot set — we choose what the cache
holds, since the vendor exposes no per-graph eviction (§2.1)"*. Nothing implemented
that choice: there is no TTL, no size bound and no cold-set pass anywhere in
`tortoise/` (the only deletes are targeted projections by id/url/session, plus the
`cypher_guard` that REFUSES an unscoped one). So "derived hot cache" described
intent, not a property of the system.

⛔ THE MECHANISM MEASUREMENT — this is why the module is a GATE and not a sweeper.

A server-side eviction policy cannot evict a cold NODE, because **a FalkorDB graph
is ONE Redis key**. Measured on the lane container (`fdb-lane-9361951d11`,
`127.0.0.1:16412`), 20,000 `:Point` nodes, then `maxmemory-policy allkeys-lru` with
`maxmemory` below the resident size::

    evicted_keys: 2
    KEYS: ['pressure0']              # the graph key is GONE from the keyspace
    MATCH (n) RETURN count(n) -> 0   # 20,000 -> 0, silently, no error raised
    GRAPH.LIST -> ['evict_probe']    # on THAT build, STILL NAMED THE GRAPH

So the eviction unit is the whole graph, and the loss is silent: no exception, and no
count that survives it. ⚠️ ``GRAPH.LIST`` is NOT a reliable detector of it — the last
line above is what that build reported, while an independent re-measurement on
``falkordb-server:v4.20.6`` after the same class of eviction saw ``GRAPH.LIST -> []``.
Which of the two you get is a version detail, so nothing here may key a verdict on it
(the census does not: ``tools/cache_eviction_census.py`` lists first only to keep a
typo'd graph name from being CREATED, and an evicted-but-listed graph is still refused
by the read itself — ``GRAPH.RO_QUERY`` answers ``Invalid graph operation on empty
key``, which is a :class:`CacheReadError`). Redis-level TTL is the same unit — the only
key an operator could expire is the graph itself (`ttl=-1` on every key measured,
including the per-graph `telemetry{}` stream) — and FalkorDB's own Enterprise offload
confirms the granularity: *"serialize a graph to disk and evict it from memory, leaving a
lightweight stub"*, where *"removing a stub in any way — `DEL`, `UNLINK`, TTL
expiration, or `FLUSHALL` — deletes the matching dump file"*
(`docs.falkordb.com/enterprise/operations/graph-offloading`). Node-granular
eviction therefore has to be **application-side**, and the vendor's own shape for it
is a watermark-driven background sweep (`falkordbe.memory-pressure` +
`idle-threshold-ms` + `evict-interval-ms`) — at graph granularity.

⛔ THE ORDERING CONSTRAINT — the whole reason this module refuses.

`#10976` states it and this module implements it verbatim: *"Eviction must not run
before the record can serve. Evicting a node whose only copy is the graph destroys
it … this is gated on #10593 + #7879, and its first deliverable is the census plus a
dry-run pass that reports what it WOULD evict and which of those nodes the record
can serve. **A pass that cannot answer that must refuse, never sweep.**"*

Both gates are OPEN as of this writing (`#10593` — every hosted record write is
rejected `22P02` — and `#7879`, the record epic), so today the correct answer for
every class is `cannot-answer` and the verdict is `REFUSED`. The point of writing it
down as code, rather than as prose, is that the day the gates close the verdict
flips by itself, and that a future sweeper cannot be written without consuming a
verdict that says which classes were proven.

WHAT THIS MODULE IS NOT
-----------------------
It is not the parity instrument. `tools/turn_parity_census.py` (#10029) already owns
the two store legs, the paging guard, the tenant-scoped record read and the
field-by-field parity definition for the ONE class that has an instrument (turns).
This module **consumes** that report; it never re-implements the INSTRUMENT, and it never
invents a verdict for a class no instrument covers. A class with no instrument is
`cannot-answer`, which is a refusal — never a silent skip and never an assumed pass.
⚠️ Its ONE mirrored statement is :data:`POPULATION_RESIDENT_COUNT` — a copy of the
instrument's own count predicate, used only to BOUND the report's licence, and pinned by
an identity test. That is not a second instrument: it derives no count the licence spends.

READ-ONLY, AND STRUCTURALLY SO. Nothing here writes, deletes or drops anything: the
only statements it issues are counts, and the only other commands are `INFO memory`,
`EXISTS`/`TTL` on the graph's own key, and `GRAPH.MEMORY USAGE` reads (no `KEYS`, no
`CONFIG GET`, no `CONFIG SET`) — and every count goes through the graph's
**read-only verb** — `ro_query`, i.e.
`GRAPH.RO_QUERY` — never `graph.query`. The `DELETE`-bearing arm does not exist to
be called by mistake, and the tests assert that no refused assessment emitted a
mutating statement.

⛔ WHY THE VERB IS PART OF THE CONTRACT, not a stylistic preference. `graph.query`
is `GRAPH.QUERY`, which **CREATES the graph** when the name does not exist; a census
whose whole justification is *"changes nothing"* cannot count through it. Measured
in this repo three times: `tools/engine_probe.py` (*"QUERY MINTS the graph when it
does not exist — a probe against a missing name created it and it appeared in
`GRAPH.LIST`"*), `tools/backfill_graph_turns.py` and `tools/edge_census.py`, the
last two shipping `_assert_graph_exists` for exactly this reason. `GRAPH.RO_QUERY`
answers `-ERR Invalid graph operation on empty key` for a missing graph and creates
nothing — so a typo'd graph name is a REFUSAL here, never a store mutation.
"""
from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "CACHE_CEILING_ENV",
    "CACHE_EVICTION_EXIT_CLEAN",
    "CACHE_EVICTION_EXIT_PLAN",
    "CACHE_EVICTION_EXIT_REFUSED",
    "CACHE_EVICTION_EXIT_UNBOUNDED",
    "CACHE_EVICTION_EXIT_USAGE",
    "DERIVED_LABELS",
    "PARITY_INSTRUMENTS",
    "POPULATION_RESIDENT_COUNT",
    "TRUTH_LABELS",
    "Assessment",
    "CacheReadError",
    "CacheState",
    "ClassReading",
    "ParityReport",
    "assess",
    "classify",
    "config_labels",
    "load_parity_report",
    "measure_state",
    "node_total",
    "population_resident_count",
    "resident_counts",
]

#: Where a declared ceiling is read from. ⛔ ABSENT means "no ceiling declared", and
#: the assessment then measures against the instance's own ``maxmemory`` and SAYS
#: that is what it did — a cache with no declared ceiling is the finding, not an
#: error, so it is reported rather than refused (#10976's Target: *"resident graph
#: bytes bounded by a declared ceiling"* — nothing declares one today).
CACHE_CEILING_ENV = "TORTOISE_CACHE_CEILING_BYTES"

# Exit codes, aligned with the two census instruments this composes
# (`tools/graph_census.py`, `tools/turn_parity_census.py`): a caller must be able
# to tell "I passed the wrong flags" from "this cache may not be evicted".
CACHE_EVICTION_EXIT_CLEAN = 0     # measured; within the declared BYTE ceiling.
                                  # ⛔ NOT "nothing to evict": `plan["evictable"]` is
                                  # published on this verdict too, and a consumer
                                  # enforcing a NODE budget may still sweep.
CACHE_EVICTION_EXIT_PLAN = 2      # over the bound; `plan["evictable"]` is the LICENCE
CACHE_EVICTION_EXIT_REFUSED = 3   # over the bound, and NOTHING is proven — nothing may go
CACHE_EVICTION_EXIT_UNBOUNDED = 4  # there is no bound anywhere (see `CacheState`)
CACHE_EVICTION_EXIT_USAGE = 1     # flag/IO mistake

# ⛔ EXIT 2 IS NOT "the whole cache may go" AND MUST NEVER BE READ AS IT.
# It means: *the populations listed in `plan["evictable"]` are proven record-served and
# may be evicted; every other class is listed in `problems` and may NOT be touched.* A
# partial plan is safe by construction (evicting exactly the proven populations still
# satisfies #10976's "0 evictions of a node the record cannot serve"), but a sweeper
# that reads exit 2 as a blanket licence would sweep the unproven classes too. A
# caller MUST read `plan["evictable"]`; `plan["not_evictable"]` carries the rest.

#: §3's split, as the labels it names. **Authority, not location** — §3 is explicit:
#: *"READ THE AUTHORITY COLUMN AS AUTHORITY, NOT AS LOCATION"*, and Postgres is the
#: record for the derived layer too. So membership here decides only **which
#: instrument must answer "can the record serve this?"**, never whether a class is
#: evictable: every class is evictable the moment the record is proven to serve it.
TRUTH_LABELS = frozenset({"Source", "Event", "GraphEvent"})

#: §3's derived layer: *"the Semantic, Epistemic and Procedural layers — all of it a
#: fold over the truth"*, journal-carried, updated in place.
DERIVED_LABELS = frozenset({"Subject", "Object", "Point", "Operator"})

#: The POPULATIONS that have a parity instrument — and **one entry is the finding**.
#: `tools/turn_parity_census.py` (#10029) proves the record serves the turn
#: population, field by field, per session. Nothing else in the repo proves that for
#: any other population, so nothing else can be answered and nothing else may be
#: evicted. Adding a population here is the work #10976's egress half actually needs.
#:
#: ⚠️ A population is NOT a label, and the difference is load-bearing: turns are
#: `:Point` nodes (`is_episodic = true`), so a CLEAN turn census must NOT be read as
#: licensing the eviction of every `:Point` — or of `:Source`, or of `:GraphEvent`.
#: That is why eligibility is keyed by population and why the turn population's node
#: count is taken from the instrument's own report (`measured_nodes`) rather than
#: re-derived here. ⚠️ This module carries exactly ONE copy of the turn PREDICATE —
#: :data:`POPULATION_RESIDENT_COUNT`, a mirror pinned by
#: `test_the_population_count_mirrors_the_instrument` — and uses it only to BOUND the
#: report, never to derive the count the licence spends.
PARITY_INSTRUMENTS: dict[str, str] = {
    "turn": "tools/turn_parity_census.py",
}

#: How to count each population's OWN resident nodes in the graph being censused.
#:
#: ⛔ WHY THIS EXISTS — the licence's only staleness guard used to be the WHOLE graph's
#: node total (:func:`node_total`, `MATCH (n) RETURN count(n)`), which is the wrong
#: denominator: a stale `turn` report claiming 20,000 nodes passed on a graph holding
#: 25,000 nodes in total but only **500** in the turn population (500 < 20,000 <
#: 25,000), so ``plan["evictable"][i]["nodes"]`` — the cap
#: ``cache_evictor``/``licence_remaining`` spends — became **40x** the population it
#: bounds. Nothing unserved could be deleted (gate 3 asks the record per node), but the
#: property the cap exists for — *"evicting more nodes than the instrument measured is
#: evicting nodes nothing proved"* — was weakened by **40x** on a small population (the
#: multiple is the report's claim over the real population; nothing here is a square, so
#: do not restate it as an order of magnitude). Measured on a lane container:
#: `docs/evidence/10976-licence/`.
#:
#: ⚠️ THE PREDICATE IS THE INSTRUMENT'S, NOT THIS MODULE'S. `tools/turn_parity_census.py`
#: owns the turn population's shape (#3551) — ``_GRAPH_EPISODIC_COUNT_CYPHER`` — and this
#: statement is a MIRROR of it, kept literally identical (same label, same property, same
#: `RETURN count(...)`) and pinned by
#: ``tests/test_cache_eviction_census.py::test_the_population_count_mirrors_the_instrument``:
#: a consumer may not import a script, so the only way to keep ONE definition in practice
#: is a test that reds when the two drift. Mirror it exactly — ``is_episodic`` is also
#: stamped on ``:Source``/``:Object`` nodes (``graph-scripts/backfill_is_episodic.py``), so
#: dropping the ``:Point`` half would INFLATE the ceiling and re-open exactly the hole
#: this closes.
#:
#: A population registered in :data:`PARITY_INSTRUMENTS` with no entry here is a REFUSAL
#: (:func:`population_resident_count`), never a defaulted bound.
POPULATION_RESIDENT_COUNT: dict[str, str] = {
    "turn": "MATCH (t:Point) WHERE t.is_episodic = true RETURN count(t)",
}

#: The verdict a parity instrument must reach for its class to be provable. These
#: are `turn_parity_census`'s own vocabulary, quoted rather than re-derived: a
#: report in any other verdict did NOT prove the record serves the class.
PARITY_PROVEN_VERDICTS = frozenset({"CLEAN"})


def config_labels() -> frozenset[str]:
    """The preserved config classes — projection's registry, never a second copy.

    §3's invariant is ``derived = replay(journal) ∪ preserved_config``, and
    ``projection._config_classes()`` is the ONLY accessor for that second term
    (memoised, cycle-safe by construction — see its own docstring). Re-listing the
    labels here would be a second owner of the set, and the two would drift the
    first time a pack is added.

    ⚠️ A failure to read the registry is NOT softened to an empty set. An empty
    config set would silently move every config node into the "no instrument"
    bucket, which happens to be the safe direction today and the WRONG one the day
    a class is proven — so the exception propagates and the caller refuses.
    """
    from tortoise.projection import _config_classes

    return frozenset(cls.label for cls in _config_classes())


@dataclass(frozen=True)
class ParityReport:
    """What a parity instrument said, for one population.

    ``instrument`` is the tool's path; ``verdict`` is that tool's own word, kept
    verbatim so this module never has to agree with a paraphrase of it. The population
    pair is read from the instrument's REAL report shape (see
    :func:`load_parity_report`), and ``graph_name`` is the graph the report says it
    measured.

    ``graph_name`` is CHECKED, not asserted: the report must carry its own ``graph``
    (``tools/turn_parity_census.py::census`` emits it), :func:`load_parity_report`
    refuses a report that carries none, and a name that disagrees with the graph being
    measured is refused too. ⚠️ What that buys is a real comparison, not a proof: the
    reader still trusts the instrument to name the graph it actually opened, the reader
    cannot correlate a name with an INSTANCE (two hosts can both call a graph
    ``tortoise``), and a census of the SAME graph measured EARLIER passes this check
    untouched — time is not part of the identity. Downstream, :func:`assess` applies TWO
    staleness guards: the PRIMARY one bounds the report against its POPULATION's own
    resident count (:data:`POPULATION_RESIDENT_COUNT`), and the whole-graph over-count
    check REMAINS as a backstop for a claim above every node the graph holds. Stated here
    rather than overclaimed: an earlier version of this docstring called the field "an
    OPERATOR ASSERTION" and the loader filled in a missing name with the caller's own —
    which made the check unable to fail at all (review P1 on #11004).
    """

    instrument: str
    verdict: str
    measured_nodes: int | None = None
    total_nodes: int | None = None
    graph_name: str | None = None

    def proves(self) -> bool:
        """True only when the instrument RAN, was CLEAN, and covered EVERYTHING.

        ⛔ Strict on purpose. Every relaxed form licenses a sweep over nodes nothing
        measured:

        - ``measured == total`` exactly — not ``>=``. An instrument that measured
          MORE nodes than the population is measuring a different population, not
          proving this one.
        - ``total > 0`` — ``0/0`` is not a proof, it is a census of nothing, and it
          would flip REFUSED→PLAN with nothing behind it.
        - both counts present and non-negative.
        """
        if self.verdict not in PARITY_PROVEN_VERDICTS:
            return False
        if self.measured_nodes is None or self.total_nodes is None:
            # A verdict with no population is not evidence about a population.
            return False
        if self.total_nodes <= 0 or self.measured_nodes < 0:
            return False
        return self.measured_nodes == self.total_nodes


@dataclass(frozen=True)
class CacheState:
    """The measured cache — size, ceiling and the mechanisms that do NOT exist.

    ⚠️ ``resident_bytes`` is ``INFO memory used_memory``, which is the whole
    INSTANCE, because that is the only exact figure the engine gives. The per-graph
    ``GRAPH.MEMORY USAGE`` is carried separately and never substituted for it: §2.1
    measured it as a sampling ESTIMATE that *"does NOT include per-graph/Redis-key
    overhead"*, is blind to the HNSW vector index (#5331, 8.5x-21.7x under-report),
    and both the doc and `tortoise/graph_storage.py` forbid wiring it to a cap. A
    ceiling wired to the wrong figure is worse than no ceiling.
    """

    graph_name: str
    resident_bytes: int
    maxmemory_bytes: int
    maxmemory_policy: str
    ceiling_bytes: int | None
    ceiling_source: str
    graph_key_ttl_seconds: int | None
    keys_per_graph: int
    graph_memory_usage_mb: float | None
    graph_memory_usage_note: str | None

    @property
    def bound_bytes(self) -> int | None:
        """The figure the resident size is judged against, or ``None`` if UNBOUNDED.

        A declared ceiling wins. With none declared, the instance's own ``maxmemory``
        is the only real bound there is — a write REFUSAL, not an eviction — and
        ``ceiling_source`` says so on every reading so nobody reads a provider cap as
        a policy choice.

        ⛔ ``maxmemory 0`` means **unlimited** in Redis, so it is reported as *no
        bound at all* rather than as a bound of zero: a bound of 0 would make every
        reading "over by its whole size" and hide the one state that most needs
        naming — a cache with no ceiling anywhere.
        """
        if self.ceiling_bytes is not None:
            return self.ceiling_bytes
        if self.maxmemory_bytes <= 0:
            return None
        return self.maxmemory_bytes

    @property
    def over_ceiling(self) -> bool:
        bound = self.bound_bytes
        return bound is not None and self.resident_bytes > bound


@dataclass(frozen=True)
class ClassReading:
    """One resident class, and whether anything can prove the record serves it."""

    label: str
    nodes: int
    authority: str          # 'truth' | 'derived' | 'config' | 'unclassified'
    instrument: str | None  # the parity tool that could answer, if one exists
    proven: bool            # the instrument ran, was CLEAN, and covered the class
    eligible: bool          # proven ⇒ may be evicted
    reason: str
    #: The BARE population name (``turn``) when this row is a population rather than
    #: a label, else ``None``. ⛔ ``label`` is a DISPLAY string (``"turn
    #: (population)"``) precisely so a label row and a population row that name the
    #: same nodes are distinguishable in the report; a licence keyed on the display
    #: string is not the same word the consumer is asked for, so
    #: ``plan["evictable"][i]["population"]`` reads THIS field. Measured on the
    #: executor (``tortoise/cache_evictor.py``, adopted from its branch): its
    #: population gate compares for equality, so a licence naming ``"turn
    #: (population)"`` refused the ``turn`` sweep it had just licensed — fail-closed,
    #: but a licence no consumer can spend is not a licence.
    population_name: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "population": self.population_name,
            "nodes": self.nodes,
            "authority": self.authority,
            "instrument": self.instrument,
            "proven": self.proven,
            "eligible": self.eligible,
            "reason": self.reason,
        }


@dataclass
class Assessment:
    """The census: state, per-class verdicts, and the refusal when one is unproven."""

    state: CacheState
    classes: tuple[ClassReading, ...]
    verdict: str            # 'CLEAN' | 'PLAN' | 'REFUSED' | 'UNBOUNDED'
    problems: list[str] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        """True whenever the cache must NOT be swept — including when it is UNBOUNDED.

        ⛔ `UNBOUNDED` belongs here. A sweeper written as
        `if not census.refused: execute(census.plan["evictable"])` is the natural
        reading of this property's name, and leaving UNBOUNDED out of it would sweep a
        cache the tool says nothing may be sized against — the same fail-open shape as
        reporting `over_ceiling: false`.
        """
        return self.verdict in {"REFUSED", "UNBOUNDED"}

    @property
    def eligible_classes(self) -> tuple[ClassReading, ...]:
        return tuple(c for c in self.classes if c.eligible)

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue": 10976,
            "verdict": self.verdict,
            "problems": list(self.problems),
            "state": {
                "graph_name": self.state.graph_name,
                "resident_bytes": self.state.resident_bytes,
                "bound_bytes": self.state.bound_bytes,
                "ceiling_bytes": self.state.ceiling_bytes,
                "ceiling_source": self.state.ceiling_source,
                "maxmemory_bytes": self.state.maxmemory_bytes,
                "maxmemory_policy": self.state.maxmemory_policy,
                # ⛔ None, never False, when there is no bound: a monitor keyed on
                # this machine-readable field would read `false` as "bounded and fine"
                # on a cache nothing bounds — the same false negative the UNBOUNDED
                # verdict exists to prevent, one field over.
                "over_ceiling": (None if self.state.bound_bytes is None
                                 else self.state.over_ceiling),
                "graph_key_ttl_seconds": self.state.graph_key_ttl_seconds,
                "keys_per_graph": self.state.keys_per_graph,
                "graph_memory_usage_mb": self.state.graph_memory_usage_mb,
                "graph_memory_usage_note": self.state.graph_memory_usage_note,
            },
            "classes": [c.as_dict() for c in self.classes],
            "plan": self.plan,
        }


class CacheReadError(RuntimeError):
    """The cache could not be read — a REFUSAL, never a zero-byte cache."""


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def measure_state(
    client: Any,
    graph_name: str,
    *,
    ceiling_bytes: int | None = None,
) -> CacheState:
    """Read the cache's real size and the mechanisms it does not have.

    ``client`` is a redis client (the same one a FalkorDB graph handle carries).
    Every field is read, never inferred: an unreadable ``INFO`` raises
    :class:`CacheReadError` rather than reporting 0 bytes, because "0 bytes" and
    "could not read" are opposite findings and only one of them is safe.
    """
    try:
        info = client.info("memory")
    except Exception as exc:
        raise CacheReadError(f"INFO memory unreadable: {exc}") from exc

    resident = _int_or_none(info.get("used_memory"))
    maxmemory = _int_or_none(info.get("maxmemory"))
    if resident is None or maxmemory is None:
        raise CacheReadError(
            "INFO memory did not carry used_memory/maxmemory — refusing to guess a size"
        )
    policy = str(info.get("maxmemory_policy") or "")

    # The graph's OWN keys. MEASURED: a graph is one `graphdata` key plus a
    # `telemetry{<graph>}` stream — so `keys_per_graph` is the number of Redis keys a
    # server-side eviction policy could take away to reclaim ONE graph, and it is the
    # evidence that the eviction unit is the graph.
    #
    # ⛔ PROBED BY NAME, never `KEYS *`. `KEYS` is an O(N) full-keyspace scan on a
    # single-threaded server, so using it here would stall the very cache this census
    # exists to protect — on a production instance, as a side effect of measuring it.
    # The candidate names are already known, so there is nothing to scan for.
    graph_key_ttl: int | None = None
    keys_per_graph = 0
    try:
        for name in (graph_name, f"telemetry{{{graph_name}}}"):
            if client.exists(name):
                keys_per_graph += 1
                if name == graph_name:
                    graph_key_ttl = _int_or_none(client.ttl(name))
    except Exception as exc:
        raise CacheReadError(f"EXISTS/TTL for {graph_name!r} unreadable: {exc}") from exc

    declared = ceiling_bytes
    # ⛔ CARRY THE PROVENANCE. `ceiling_source` exists to answer "where did this bound
    # come from", so hard-coding the env-var name whenever ANY ceiling arrived made it
    # state something false: `--ceiling-bytes 100` with the env var unset reported
    # `declared (TORTOISE_CACHE_CEILING_BYTES)`.
    source = None
    if declared is not None:
        source = "declared (--ceiling-bytes)"
    if declared is None:
        raw = os.environ.get(CACHE_CEILING_ENV)
        if raw is not None and raw.strip() != "":
            try:
                declared = int(raw)
            except ValueError as exc:
                raise CacheReadError(
                    f"{CACHE_CEILING_ENV}={raw!r} is not an integer byte count"
                ) from exc
            source = f"declared ({CACHE_CEILING_ENV})"
    if declared is not None and declared <= 0:
        # A non-positive ceiling is not a bound: 0 would make every reading "over by
        # its whole size" and a negative one would make `over_by_bytes` nonsense while
        # still emitting the LICENCE exit code on a mistyped flag.
        raise CacheReadError(
            f"declared ceiling {declared} is not a positive byte count — a bound must "
            f"be greater than zero"
        )
    source = (
        source if source is not None
        else ("instance maxmemory (⛔ NOT a declared ceiling — the bound is a write refusal)"
              if maxmemory > 0 else
              "⛔ NO BOUND ANYWHERE — no ceiling declared and maxmemory 0 (unlimited)")
    )

    usage_mb: float | None = None
    usage_note: str | None = None
    try:
        from tortoise.graph_storage import measure_graph_storage

        # ⚠️ The CLIENT, not a graph handle: `measure_graph_storage` issues
        # `execute_command("GRAPH.MEMORY", ...)`, which a `falkordb.Graph` does not
        # expose. Passing the handle made this field permanently `None` on a real
        # run — a reading that never exists is worse than an absent field.
        reading = measure_graph_storage(client, graph_name, samples=1000)
        payload = reading.as_dict()
        if payload.get("graph_storage_ok"):
            usage_mb = _float_or_none(payload.get("graph_storage_mb"))
            usage_note = payload.get("graph_storage_precision_note")
        else:
            usage_note = f"unavailable: {payload.get('graph_storage_error')}"
    except Exception as exc:
        usage_note = f"unavailable: {type(exc).__name__}: {exc}"

    return CacheState(
        graph_name=graph_name,
        resident_bytes=resident,
        maxmemory_bytes=maxmemory,
        maxmemory_policy=policy,
        ceiling_bytes=declared,
        ceiling_source=source,
        graph_key_ttl_seconds=graph_key_ttl,
        keys_per_graph=keys_per_graph,
        graph_memory_usage_mb=usage_mb,
        graph_memory_usage_note=usage_note,
    )


#: The shape a Cypher identifier may be emitted BARE — restated here as the strict
#: full-match the shared rule's `$` does not give us. Kept local on purpose: this is a
#: REFUSAL to emit a bare form, not a second quoting rule (the quoting itself is
#: always `_quote_identifier`'s).
_BARE_SAFE = r"[A-Za-z_][A-Za-z0-9_]*"


def _safe_label(name: str, quote: Any) -> str:
    """*name* as a Cypher label, never merely "whatever the shared rule returned".

    ⛔ WHY THIS IS NOT REDUNDANT WITH ``_quote_identifier``. That rule tests bare-ness
    with ``re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name)``, and Python's ``$`` matches
    BEFORE a trailing newline — so ``"Point\n"`` is returned BARE and unquoted, and
    ``MATCH (n:Point\n)`` parses as label ``Point`` (measured: ``GRAPH.EXPLAIN`` →
    PARSED OK; and ``CREATE (n:`Point\n`)`` also parses, so such a label exists).
    With both labels resident, each row is counted against ``Point`` — a wrong census
    number, and a negative ``<unlabelled>``.

    So the bare form is trusted only when it is a STRICT full match; everything else
    goes through the shared rule's quoting path (imported, never re-implemented).
    """
    quoted = quote(name)
    if not quoted.startswith("`") and not re.fullmatch(_BARE_SAFE, quoted):
        # ⛔ REJECT, do not re-quote. Re-quoting here would mean writing a second
        # copy of the escaping rule (the shared one doubles interior backticks),
        # and a census that guesses an escape is how the miscount this guard exists
        # to prevent comes back. A label that is neither a strict identifier nor
        # already quoted by the shared rule cannot be measured safely, so the
        # census refuses the whole reading instead of counting one label as another.
        raise CacheReadError(
            f"label {name!r} is not a strict Cypher identifier and the shared "
            f"quoting rule returned it bare ({quoted!r}) — refusing to attribute "
            f"counts to a label this census cannot address unambiguously"
        )
    return quoted


def _read_statement(graph: Any, statement: str, what: str) -> list[Any]:
    """Run *statement* through the graph's READ-ONLY verb, or REFUSE.

    ⛔ ``graph.query`` IS ``GRAPH.QUERY``, AND ``GRAPH.QUERY`` MINTS THE GRAPH. The
    statement text of a count is innocent; the VERB that carries it is not — a
    `MATCH (n) RETURN count(n)` against a name that does not exist leaves an empty
    graph of that name behind (measured; see the module docstring). On a typo'd
    ``--graph`` — where both halves can carry the same typo, since the ``--graph`` /
    URI equality check only proves the operator typed the same wrong name twice —
    that would CREATE a graph on the production instance and then report a verdict
    about it, indistinguishable from a genuinely empty cache.

    ``ro_query`` is ``GRAPH.RO_QUERY``, which answers ``-ERR Invalid graph operation
    on empty key`` and creates nothing. This is the distinction
    ``tortoise/cypher_guard.py`` names in the same words: ``read_only`` selects the
    VERB.

    ⛔ NO FALLBACK to ``query``. A handle that exposes no read-only verb is a
    REFUSAL, not a reason to "keep measuring" through the one statement that can
    write: a duck-typed stub is exactly the handle most likely to lack it, and a
    fail-open here is the property this module's whole contract rests on.
    """
    read = getattr(graph, "ro_query", None)
    if not callable(read):
        raise CacheReadError(
            f"{what} cannot be read: this graph handle exposes no read-only verb "
            f"(`ro_query`), and `query` is GRAPH.QUERY — which CREATES the graph when "
            f"the name does not exist. A census that must change nothing cannot use "
            f"it, so this is a refusal rather than a fallback."
        )
    try:
        return list(read(statement).result_set)
    except Exception as exc:
        raise CacheReadError(f"{what} unreadable (GRAPH.RO_QUERY): {exc}") from exc


def resident_counts(graph: Any) -> dict[str, int]:
    """Node count per label, read from the graph. A label is never inferred.

    ``CALL db.labels()`` is the enumeration, and a node carrying NO label is counted
    under ``<unlabelled>`` (measured directly, never as a residual — see below): a
    census that silently dropped what it could not classify would be a number an owner
    decides on while looking at a smaller cache than exists — the same rule
    `tools/graph_census.py` states.

    ⚠️ The per-label counts therefore do NOT sum to the node total, and that is
    correct: a multi-label node is resident under each of its labels.

    ⛔ THE LABEL IS INTERPOLATED INTO CYPHER, and it comes from the graph's own
    schema — i.e. from whatever a writer put there. A label named
    ``X) DETACH DELETE (n) //`` would turn the count into a delete. This is the shape
    `tortoise/graph_key_restore.py` names verbatim (*"a label/property name reaches
    Cypher by interpolation and comes from the graph's own schema"*), and the fix is
    that module's ONE identifier-quoting rule — imported, never copied, so a second
    quoting rule cannot drift from the first (`tools/dod_5090_probe.py` is the
    sibling census that made the same move). `tortoise/security.py` uses an allowlist
    for the query runners instead; a census cannot allowlist, because reporting
    labels it has never seen IS its job.
    """
    from tortoise.graph_key_restore import _quote_identifier

    labels = [row[0] for row in
              _read_statement(graph, "CALL db.labels()", "the label enumeration")]

    counts: dict[str, int] = {}
    for label in labels:
        rows = _read_statement(
            graph,
            f"MATCH (n:{_safe_label(str(label), _quote_identifier)}) RETURN count(n)",
            f"the count for label {label!r}",
        )
        counts[label] = int(rows[0][0])

    # ⛔ MEASURED, not derived as a residual. Labels do NOT partition the node set:
    # multi-label nodes are real here (`(n:Point:Object)`, `(e:Object:Event)`,
    # `(n:Document:Source)` — and the #5140 relabel migration creates them), so a node
    # carrying two labels is counted once per label and `total - sum(counts)` goes
    # NEGATIVE. A negative node count in the artifact an owner decides on is worse than
    # no residual at all, so label-less nodes are counted directly.
    rows = _read_statement(
        graph, "MATCH (n) WHERE size(labels(n)) = 0 RETURN count(n)",
        "the unlabelled node count",
    )
    unlabelled = int(rows[0][0]) if rows else 0
    if unlabelled:
        counts["<unlabelled>"] = unlabelled
    return counts


def node_total(graph: Any) -> int:
    """The graph's TRUE node count — one node counted once.

    ⛔ NOT `sum(resident_counts().values())`. Labels do not partition the node set: a
    multi-label node (`(n:Point:Object)`, real in this repo) is counted once per label,
    so the label sum OVER-states the population. Using it as the ceiling on a proven
    report would let a stale report claiming between the true N and the inflated sum
    produce a plan offering more nodes than the graph holds — the exact over-offer the
    cross-check exists to prevent.

    ``MATCH (n) RETURN count(n)`` is answered from graph metadata (measured 0.000 s on a
    20,000-node lane graph; EXPLAIN is a bare `Project`, no scan), so this is also the
    cheap form — unlike the label-scan `size(labels(n)) = 0` query in
    :func:`resident_counts`.
    """
    rows = _read_statement(graph, "MATCH (n) RETURN count(n)",
                           "the total node count")
    return int(rows[0][0])


def population_resident_count(graph: Any, population: str) -> int:
    """The *population*'s OWN resident node count in THIS graph.

    This is the ceiling a licence may not exceed (see
    :data:`POPULATION_RESIDENT_COUNT`), read through the same read-only verb as every
    other census statement (:func:`_read_statement`) — a census cannot create the graph
    it is measuring, even while proving a bound.

    ⛔ A POPULATION WITH NO COUNT STATEMENT IS A REFUSAL. Defaulting to ``0`` would
    demote every report for that population (a silent, permanent shutdown of a gate
    nobody would notice was shut — REFUSED looks like the record not being ready), and
    defaulting to the whole-graph total would silently restore the defect this exists
    to close. Neither is a bound, so the caller is told instead.

    ⛔ A FAILING READ PROPAGATES, never softening to a number. ``0`` and "could not
    read" are opposite findings — the same rule :func:`node_total` and
    :func:`measure_state` follow — and only one of them is safe to threshold against.
    That includes an EMPTY result: ``RETURN count(...)`` always yields one row, so no
    rows means the reading did not happen. Defaulting to ``0`` would demote every report
    for the population with the message *"this graph holds only 0 …"* — a fact about the
    graph that nobody measured, and a REFUSAL indistinguishable from the record being
    unready (the silent shutdown the missing-statement branch above also refuses).
    """
    statement = POPULATION_RESIDENT_COUNT.get(population)
    if statement is None:
        raise CacheReadError(
            f"population {population!r} is registered in PARITY_INSTRUMENTS but has no "
            f"resident-count statement in POPULATION_RESIDENT_COUNT — a licence for it "
            f"cannot be bounded, so it is refused rather than bounded by a guess"
        )
    rows = _read_statement(
        graph, statement, f"the resident {population} population count"
    )
    if not rows:
        raise CacheReadError(
            f"the resident {population} population count returned NO row — the "
            f"population size is UNMEASURED, and an unmeasured size cannot bound a "
            f"licence"
        )
    return int(rows[0][0])


def classify(label: str, *, config: Iterable[str]) -> str:
    """§3's authority for *label*, never a guess.

    §3's mechanical test is *"is this write carried by the journal?"*. The label sets
    are named in §3 itself and the config term comes from projection's registry, so
    a label this module has not been taught is ``unclassified`` — which is a
    refusal, not a default into the safe-looking bucket.
    """
    if label in config:
        return "config"
    if label in TRUTH_LABELS:
        return "truth"
    if label in DERIVED_LABELS:
        return "derived"
    return "unclassified"


def assess(
    client: Any,
    graph: Any,
    graph_name: str,
    *,
    ceiling_bytes: int | None = None,
    parity_reports: dict[str, ParityReport] | None = None,
) -> Assessment:
    """Answer #10976's one gated question, and refuse when it cannot be answered.

    *parity_reports* maps an **instrument path** (a value of
    :data:`PARITY_INSTRUMENTS`) to that instrument's report. A class is ``eligible``
    only when its instrument supplied a report that :meth:`ParityReport.proves` — an
    instrument that did not run, was not CLEAN, or covered part of the population
    leaves the class ``cannot-answer``.

    The verdict is computed from the WRONG direction on purpose: **an unproven class
    is never evicted.** ``PLAN`` licenses ONLY the populations named in
    ``plan["evictable"]`` — each one proved by its own instrument's report. Every other
    class is listed in ``problems`` and ``plan["not_evictable"]`` and may not be
    touched, so a partial plan is safe **by construction**: evicting exactly the
    proven populations still satisfies #10976's "0 evictions of a node the record
    cannot serve".

    ⛔ DO NOT READ ``PLAN`` AS "EVERYTHING WAS PROVEN". It is not a statement about the
    classes it omits — it is a licence scoped to the list it prints. An earlier
    revision of this docstring said "one unproven class refuses the whole sweep",
    which described a rule the function does not implement: a caller who believed it
    would write ``if census.verdict == "PLAN": sweep_every_label()`` and delete every
    ``:Point``, ``Source`` and ``GraphEvent`` in the graph — the data loss #10976
    exists to prevent, reached by trusting prose over the artefact. The artefact is
    ``plan["evictable"]``, and it is the only thing a licence covers.
    """
    state = measure_state(client, graph_name, ceiling_bytes=ceiling_bytes)
    counts = resident_counts(graph)
    config = config_labels()
    reports = dict(parity_reports or {})

    classes: list[ClassReading] = []
    problems: list[str] = []

    for label in sorted(counts, key=lambda name: (-counts[name], name)):
        nodes = counts[label]
        if nodes == 0:
            continue
        authority = classify(label, config=config)
        if authority == "unclassified":
            reason = (f"unclassified: label {label!r} is in neither §3's truth set, its "
                      f"derived set, nor projection's config registry — no rule says "
                      f"what may become of it")
        else:
            reason = (f"cannot-answer: no parity instrument covers the {authority} label "
                      f"{label!r} as a population, so nothing proves the record serves "
                      f"these {nodes} node(s)")
        problems.append(f"{label} ({nodes} node(s)): {reason}")
        classes.append(
            ClassReading(
                label=label,
                nodes=nodes,
                authority=authority,
                instrument=None,
                proven=False,
                eligible=False,
                reason=reason,
            )
        )

    # The populations that DO have an instrument. Their node count is the
    # instrument's own measurement — this module never re-derives a population shape
    # (`turn_parity_census.py` owns the structural turn predicate, #3551).
    for population, instrument in sorted(PARITY_INSTRUMENTS.items()):
        report = reports.get(instrument)
        # ⛔ TWO WAYS A BINDING CAN FAIL, and BOTH demote. A report naming ANOTHER graph
        # is the one this used to catch; a report naming NO graph slipped through the
        # `graph_name is not None` guard, so a `ParityReport` built without a binding was
        # eligible at the library surface without ever being compared to the graph being
        # evicted — the "no binding at all" hole `ParityReport`'s docstring forbids.
        unbound = report is not None and report.graph_name is None
        mismatched = (report is not None and report.graph_name is not None
                      and report.graph_name != graph_name)
        if unbound or mismatched:
            # ⛔ ENFORCED HERE, not only at the CLI's flag surface: this library is what
            # a future sweeper is meant to consume, so an unbound report (or a CLEAN
            # census of graph B) must not license a plan that evicts graph A's nodes
            # through the API either. Demoted to unproven — the same shape and severity
            # as the size demotion below.
            reports[instrument] = None
            report = None
        if unbound:
            measured = None
            proven = False
            reason = (f"cannot-answer: {instrument}'s report carries NO graph binding "
                      f"— an unbound report cannot prove which graph it measured, so "
                      f"it licenses no graph at all")
        elif mismatched:
            measured = None
            proven = False
            reason = (f"cannot-answer: {instrument}'s report was measured against a "
                      f"different graph — a CLEAN census of one graph does not "
                      f"license evicting another")
        elif report is None:
            reason = (f"cannot-answer: {instrument} did not run — the {population} "
                      f"population is unproven")
            measured = None
            proven = False
        elif not report.proves():
            reason = (f"cannot-answer: {instrument} reported {report.verdict!r} with "
                      f"{report.measured_nodes}/{report.total_nodes} node(s) measured — "
                      f"that does not prove the record serves the {population} population")
            measured = report.measured_nodes
            proven = False
        else:
            reason = (f"proven: {instrument} reported {report.verdict!r} over "
                      f"{report.measured_nodes}/{report.total_nodes} node(s)")
            measured = report.measured_nodes
            proven = True
            # ⛔ THE LICENCE MAY NOT EXCEED THE POPULATION IT BOUNDS. `proves()` says
            # the report was CLEAN over its OWN measured population — it says nothing
            # about that population's size in THIS graph NOW. A report measured before a
            # purge (or on a graph that held more turns) therefore licensed a cap far
            # above the population, and gate 2 spends that cap directly
            # (`cache_evictor.licence_remaining`). Measured before this check: a graph
            # with 500 turn nodes and a report claiming 20,000 licensed **20,000** — 40x
            # the population — because 20,000 was still under the 25,000-node whole-graph
            # total. The comparison is against the population's own count, and it is an
            # upper bound (`>`), not equality: turns that ARRIVED since the report are
            # growth, and every one of them still has to pass gate 3 individually.
            resident_population = population_resident_count(graph, population)
            if measured > resident_population:
                proven = False
                reason = (
                    f"cannot-answer: {instrument} claims {measured} {population} "
                    f"node(s) but this graph holds only {resident_population} — the "
                    f"report is stale, or it measured a different population, so it "
                    f"cannot size a sweep here"
                )
        entry = ClassReading(
            label=f"{population} (population)",
            nodes=int(measured or 0),
            authority="derived",
            instrument=instrument,
            proven=proven,
            eligible=proven,
            reason=reason,
            population_name=population,
        )
        classes.append(entry)
        if not proven:
            problems.append(f"{entry.label}: {reason}")

    # ⛔ A POPULATION THE GRAPH CANNOT HOLD IS NOT A PROOF. `measured` comes from the
    # instrument's report, so a stale report (or one about another graph) would
    # otherwise license evicting N nodes that this graph does not contain — the
    # plan's own node count is the only number a sweeper would size itself from.
    #
    # ⚠️ THIS IS THE SECOND, LOOSER CHECK, and it has a NARROW residual job the
    # arithmetic does not let it escape. The bound that matters is applied per population
    # in the loop above (:func:`population_resident_count`): the whole-graph total let a
    # report claim 40x its own population (measured, #10976 comment 6689914601 /
    # `docs/evidence/10976-licence/`).
    #
    # ⛔ WHAT THIS CATCHES, EXACTLY — the operands are ``entry.nodes`` (the REPORT'S
    # CLAIM) and the graph's whole node count, so the trigger is:
    #
    #     resident_population >= claim > node_total
    #
    # i.e. THE CLAIM ITSELF is above every node the graph holds (which implies the
    # mirror's count is at least that large — eligibility already required
    # `claim <= resident_population`). Both halves matter, and neither of the two
    # sentences that used to sit here was right:
    #
    # - A count statement whose PREDICATE is too wide (dropping the `:Point` half) never
    #   trips it: a subgraph count cannot exceed `MATCH (n) RETURN count(n)`.
    # - Nor does a statement whose query SHAPE multiplies rows (a join, a second
    #   `MATCH`, an `UNWIND`) — the comparison never reads the mirror's count, so a
    #   multiplied count alone cannot trip it. A modest multiplication (population 500
    #   counted as 2,000 on a 25,000-node graph, claim 1,500) passes BOTH guards: the
    #   claim is validated against the inflated ceiling and is under the total. No
    #   total-based comparison can separate that from "the population grew since the
    #   report" — refusing on ``resident_population > report.total_nodes`` would block
    #   legitimate growth — so that class is covered SOLELY by
    #   `test_the_population_count_mirrors_the_instrument`, and by the characterisation
    #   test that pins this residual. Keep the pin; this check does not replace it.
    #
    # What this DOES catch is a claim above every node the graph holds. ⛔ Read that
    # NARROWLY, because "whatever produced it" would be FALSE: the population bound
    # above already required ``claim <= resident_population``, and a population is a
    # subgraph of the graph (``resident_population <= node_total``), so on a QUIESCENT
    # graph the two conditions cannot both hold — this check is UNREACHABLE there, and
    # it is NOT a second detector for a misplaced predicate. It stays as a one-integer
    # backstop whose only reachable trigger is the read skew named below (#11726 item 3
    # asked for exactly this honesty after the reorder removed the addition-skew half).
    #
    # ⚠️ ITS REACH IS READ SKEW, and the read order below is what keeps that honest. The
    # total is read HERE — AFTER every per-population count — rather than beside them:
    # a graph that GAINS nodes between the two reads then satisfies
    # ``resident_population <= total`` by construction, so an honest report cannot be
    # demoted by this check. (Read before the counts, it could: a legitimate 500/500
    # report measured REFUSED when the total read answered 5, which is the false block
    # #11726 records.) What remains reachable is the opposite skew — nodes REMOVED
    # between a population count and this read.
    resident_total = node_total(graph)
    checked: list[ClassReading] = []
    for entry in classes:
        if entry.eligible and entry.nodes > resident_total:
            entry = ClassReading(
                label=entry.label, nodes=entry.nodes, authority=entry.authority,
                instrument=entry.instrument, proven=False, eligible=False,
                reason=(f"cannot-answer: {entry.instrument} claims {entry.nodes} node(s) "
                        f"but the graph holds only {resident_total} — the report is "
                        f"stale or is about another graph"),
                # ⛔ Carry the field through the demotion. Dropping it made the
                # exported artefact report `population: null` beside a row whose label
                # demonstrably names one — a null for a name the row HAS.
                population_name=entry.population_name,
            )
            problems.append(f"{entry.label}: {entry.reason}")
        checked.append(entry)
    classes = checked

    plan: dict[str, Any] = {}
    eligible = [c for c in classes if c.eligible]
    # ⛔ THE LICENCE IS EMITTED FOR EVERY VERDICT, NOT ONLY FOR `PLAN`.
    #
    # `over_ceiling` is a BYTE test (`resident_bytes > bound_bytes`), and this module's
    # byte instrument is the sampling estimate that is BLIND to the HNSW index (#5331).
    # A consumer that enforces an ENTRY bound (`TORTOISE_CACHE_MAX_NODES`) therefore
    # had its licence gated by a different unit: a cache 50x over its node budget but
    # under its byte ceiling answered `CLEAN`, the licence was `{}`, and the executor
    # refused — so the entry bound this exists to enforce was silently unenforceable in
    # exactly the population shape #10976 describes (many small turn nodes). Measured
    # symptom: 1,000,000 nodes against a declared budget of 20,000 -> verdict REFUSED,
    # `deleted: 0`.
    #
    # ⇒ The two answers are SEPARATED and both published: `verdict` is the BYTE state
    # ("is this cache over its declared byte ceiling?"), and `plan["evictable"]` is the
    # LICENCE ("which populations are proven record-served, and how many nodes each
    # proof covers"). A consumer enforcing a node budget reads the licence and its own
    # budget; it must not infer a licence from the byte verdict.
    #
    # ⚠️ This does NOT widen what may be swept: every gate a consumer applies is
    # unchanged, and a `REFUSED` verdict (nothing proven) still yields an EMPTY
    # `evictable`, so the refusal is as sharp as it was.
    licence = [
        # ⛔ `population_name`, NEVER a fallback to `label`. A fallback re-merges the
        # two namespaces this PR separated: a label row and a population row that share
        # a word would collapse into ONE licence key, which is exactly the confusion the
        # display form exists to prevent. Unreachable today (only `eligible` rows reach
        # here, and label rows are built `eligible=False`), but the day a label becomes
        # provable the fallback would silently re-merge them. `label` is emitted beside
        # it for display, so the fallback buys nothing.
        {"population": c.population_name, "nodes": c.nodes,
         "instrument": c.instrument, "label": c.label}
        for c in eligible
    ]
    not_evictable = [c.label for c in classes if not c.eligible]
    if state.bound_bytes is None:
        # ⛔ The one state that most needs naming must not share the "bounded and
        # fine" exit. A monitor keyed on exit 0 would otherwise conclude this cache
        # is within its bound when nothing bounds it at all.
        verdict = "UNBOUNDED"
        # ⛔ `evictable` is deliberately EMPTY here. Nothing may be sized against a
        # cache with no bound, so there is no licence to issue — and `refused` is True
        # for this verdict for the same reason.
        plan = {
            "evictable": [],
            "not_evictable": not_evictable,
            "resident_bytes": state.resident_bytes,
            "bound_bytes": None,
            "⚠️ no bound": (
                "no ceiling is declared and maxmemory is 0 (unlimited) — nothing "
                "bounds this cache, so `over_ceiling` cannot be evaluated"
            ),
        }
    elif not state.over_ceiling:
        verdict = "CLEAN"
        # The cache is within its BYTE ceiling — but a consumer enforcing an ENTRY
        # budget still needs the licence, because those two units are not the same
        # question and the byte instrument cannot see the answer to the other one.
        plan = {
            "evictable": licence,
            "not_evictable": not_evictable,
            "resident_bytes": state.resident_bytes,
            "bound_bytes": state.bound_bytes,
            "over_by_bytes": 0,
            "⚠️ licence vs byte verdict": (
                "`CLEAN` is the BYTE state only. `plan['evictable']` is the licence, "
                "and a consumer enforcing a NODE budget may still have work to do."
            ),
        }
    elif not eligible:
        # ⛔ THE REFUSAL. Over the bound and NOTHING is proven — so nothing may go.
        # This is the correct answer whenever NOTHING is proven: an unserved node has the
        # graph as its only copy, so a sweep would destroy it (gate 3 asks per node).
        # ⚠️ RECORD READINESS IS NOT THE REASON, and this comment said it was: `#10593`
        # closed COMPLETED on 2026-11-06 (write-healthy, measured non-vacuously), so the
        # record CAN serve; what is missing is a CLEAN parity census for the graph being
        # swept. `#7879` (the epic) remains open, which is a statement about the record's
        # role as system of record, not about whether it can serve a node today.
        verdict = "REFUSED"
        plan = {
            "evictable": [],
            "not_evictable": not_evictable,
            "resident_bytes": state.resident_bytes,
            "bound_bytes": state.bound_bytes,
            "over_by_bytes": max(0, state.resident_bytes - (state.bound_bytes or 0)),
        }
    else:
        # A partial plan is safe: it lists ONLY the proven populations, so evicting
        # exactly them still satisfies "0 evictions of a node the record cannot
        # serve". The unproven classes stay listed in `problems` with their reason.
        verdict = "PLAN"
        plan = {
            "evictable": licence,
            "resident_bytes": state.resident_bytes,
            "bound_bytes": state.bound_bytes,
            "over_by_bytes": max(0, state.resident_bytes - (state.bound_bytes or 0)),
            "not_evictable": not_evictable,
            "mechanism": "application-side sweep",
            "⚠️ population vs label": (
                "a proven population is a SUBSET of a label (turns are `:Point` nodes "
                "with `is_episodic = true`), so the label rows above and the "
                "population row can name the same nodes. The plan offers ONLY the "
                "population: reading a CLEAN turn census as licence to evict every "
                "`:Point` would evict nodes no instrument measured."
            ),
            "server_side_policy": (
                "REFUSED, measured: a FalkorDB graph is ONE Redis key, so an eviction "
                "policy takes the WHOLE graph (20,000 nodes -> 0, silently; on the "
                "measured build `GRAPH.LIST` still named it, on v4.20.6 it did not — "
                "either way the count is what refuses). `tools/test_lane.py` pins "
                "`noeviction` for exactly that reason."
            ),
        }

    return Assessment(state=state, classes=tuple(classes), verdict=verdict,
                      problems=problems, plan=plan)


def load_parity_report(path: str, *, graph_name: str) -> ParityReport:
    """Read `tools/turn_parity_census.py`'s report into a :class:`ParityReport`.

    ⛔ READ THE INSTRUMENT'S REAL SHAPE, or the gate can never legitimately open. The
    report `turn_parity_census.py` emits (`census()`'s return dict) carries:

        instrument                        "tools/turn_parity_census.py" — REQUIRED
        graph                             the graph the census MEASURED — REQUIRED
        population.episodic_graph_nodes   the DELETE SET — every node the read returned
        dry_run.would_delete_turn_nodes   the nodes it proved AND proposed to purge
        dry_run.held_back_turn_nodes      the turn nodes it could NOT prove
        verdict                           "CLEAN" | "REFUSED"

    so coverage is ``would_delete_turn_nodes`` out of ``episodic_graph_nodes``. A CLEAN
    verdict with ``held_back`` > 0 is a CLEAN over part of the population, and must not
    prove the rest — which is precisely why ``proves()`` demands equality rather than
    trusting the verdict word.

    ⚠️ A shape this cannot read is NOT silently treated as absent. It returns a report
    whose counts are ``None``, so ``proves()`` is False — but the caller is told, in
    the reason string, that the report was unreadable, so a real integration defect
    never reads as "the record cannot serve".

    *graph_name* is the graph this census is measuring, and it is **required**. The
    report's OWN ``graph`` field (``tools/turn_parity_census.py::census`` emits it) must
    agree with it, and a report carrying **no** graph identity is REFUSED — not stamped
    with the caller's name, which is what made this check unable to fail (review P1 on
    #11004). An UNBOUND :class:`ParityReport` is still refused here and demoted by
    :func:`assess`, so a library caller cannot mint one either way.
    """
    import json

    # ⛔ REQUIRED, not keyword-optional. An unbound report is the hole
    # `ParityReport`'s own docstring says must not exist: "no binding at all — lets a
    # CLEAN census of graph A license evicting graph B". Refusing the absence HERE
    # means the library's only door cannot produce one.
    if graph_name is None or not str(graph_name).strip():
        raise CacheReadError(
            f"parity report {path} needs the graph this census is measuring: an "
            f"UNBOUND report licenses every graph, which is the hole the binding "
            f"exists to close"
        )

    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except UnicodeDecodeError as exc:
        # A file nobody can DECODE is not a flag mistake: the cache must not be evicted
        # on evidence that could not be read. Same class as a malformed JSON body, one
        # input further along — and it escaped `main()` as a traceback before this.
        raise CacheReadError(f"parity report {path} is not valid UTF-8: {exc}") from exc
    except OSError:
        # A MISSING file is a usage mistake — that is the caller's flag, not the
        # store's safety — so it is re-raised unchanged for the CLI to classify.
        raise
    except ValueError as exc:
        raise CacheReadError(f"parity report {path} is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CacheReadError(f"parity report {path} is not a JSON object")

    # ⛔ THE INSTRUMENT IS REQUIRED, and a missing one is REFUSED rather than defaulted.
    # A report naming NOTHING was silently attributed to the `turn` population, so a
    # hand-written `{"verdict":"CLEAN","population":{...},"dry_run":{...}}` opened a
    # gate (verdict PLAN, exit 2, `plan["evictable"]` naming N nodes) that a real report
    # with an unrecognised name could not — the exact inversion this check's own comment
    # names. `tools/turn_parity_census.py` now emits `instrument`, so the honest path
    # reopens by itself; while no CLEAN census report exists for the graph being swept
    # this direction costs nothing, because the licence is empty either way (gate 2 —
    # and note the verdict may still be `CLEAN`, which is its BYTE state, not a licence).
    instrument = payload.get("instrument")
    if instrument is None:
        raise CacheReadError(
            f"parity report {path} names no instrument — the field is REQUIRED "
            f"({sorted(PARITY_INSTRUMENTS.values())}); a nameless report is "
            f"attributable to any population by whoever hands it over"
        )
    if instrument not in PARITY_INSTRUMENTS.values():
        raise CacheReadError(
            f"parity report {path} names instrument {instrument!r}, which is not a "
            f"registered parity instrument ({sorted(PARITY_INSTRUMENTS.values())})"
        )

    population = payload.get("population") or {}
    dry_run = payload.get("dry_run") or {}
    if not isinstance(population, dict) or not isinstance(dry_run, dict):
        raise CacheReadError(
            f"parity report {path} has a non-object population/dry_run section"
        )

    measured = dry_run.get("would_delete_turn_nodes")
    total = population.get("episodic_graph_nodes")
    # ⚠️ STALENESS ON THIS PATH IS STILL NOT CLOSED BY THE LOADER, and saying otherwise
    # was how the residual survived a review: a census of the SAME graph measured EARLIER
    # carries the same name and loads clean. What refuses it is downstream —
    # :func:`assess` compares the report's count against the POPULATION's own resident
    # count (:data:`POPULATION_RESIDENT_COUNT`), so a report claiming MORE turns than the
    # graph now holds is demoted. A report claiming FEWER is still accepted (that is a
    # cap smaller than the population, the safe direction).

    # ⛔ THE FIELD IS `graph` AND ONLY `graph`. There is no `graph_name` fallback: the
    # alias was a pre-fix shim nothing emits (`tools/turn_parity_census.py::census`
    # emits `graph`), and keeping it re-opened the exact hole this check closes —
    # `{"graph": null, "graph_name": "tortoise"}` was resolved to `"tortoise"` by the
    # `or` before the refusal below could ever see it, so an artifact with NO identity
    # was accepted on an operator-controlled field (review P2 on #11097).
    reported_graph = payload.get("graph")
    # ⛔ A REPORT THAT NAMES NO GRAPH IS REFUSED, not attributed to the caller's own.
    # Filling the field in with ``graph_name`` made the binding VACUOUS: the loader
    # authored the assertion it was supposed to check, so `mismatched` could never fire
    # and `unbound` could never be reached from this door — the only path that produces
    # a real report. A CLEAN census taken on another instance then loaded as a licence
    # to sweep THIS graph, with the operator's typed `--graph` and the repeated
    # `--parity-graph` as the whole of the evidence (review P1 on #11004).
    # `tools/turn_parity_census.py::census` emits the identity, so the honest path is
    # open; a report without one is an artifact that cannot say what it measured.
    # ⚠️ Neither this check nor any other on this path closes STALENESS: a census of
    # the SAME graph measured earlier carries the same name and passes. Downstream,
    # `assess()` bounds it against the POPULATION's own resident count (the primary
    # guard) and KEEPS the whole-graph over-count check as a backstop. The whole-graph
    # total was the ONLY comparison before this — which let a 20,000-node claim through
    # on a 500-node population (#10976, measured, 40x).
    if reported_graph is None or not str(reported_graph).strip():
        raise CacheReadError(
            f"parity report {path} names no graph — the field is REQUIRED "
            f"(`graph`, emitted by `tools/turn_parity_census.py::census`): an "
            f"unattributed report cannot be told apart from a CLEAN census of "
            f"another graph, so it licenses nothing"
        )
    if reported_graph != graph_name:
        raise CacheReadError(
            f"parity report {path} measured graph {reported_graph!r}, but this census "
            f"is measuring {graph_name!r} — a CLEAN census of one graph does not "
            f"license evicting another"
        )

    return ParityReport(
        instrument=instrument,
        verdict=str(payload.get("verdict") or ""),
        measured_nodes=_int_or_none(measured),
        total_nodes=_int_or_none(total),
        graph_name=str(reported_graph),
    )
