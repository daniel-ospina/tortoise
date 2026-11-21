"""#10976 — the cache EGRESS EXECUTOR: bounded, record-gated, lease-safe.

WHY THIS EXISTS
---------------
`tortoise/cache_eviction.py` (the *census*, PR #11004) answers **what MAY leave
FalkorDB** and refuses when nothing proves it. It is preview-only on purpose: *"There
is no ``--execute``, and that is the deliverable, not an omission"*
(`tools/cache_eviction_census.py`). What it deliberately leaves unwritten is the
other half of the seam — its own docstring says so: *"a future sweeper cannot be
written without consuming a verdict that says which classes were proven."*

This module **is** that sweeper, and it is only that: it consumes an
:class:`~tortoise.cache_eviction.Assessment` and never re-derives a class, a
population shape or a parity verdict. It adds exactly the four things a census cannot
express — a **bound it enforces**, a **selection it orders**, a **per-node
record-serve check before any delete**, and a **metric**.

⛔ THE ORDERING CONSTRAINT (#10976, verbatim)
--------------------------------------------
*"Eviction must not run before the record can serve. Evicting a node whose only copy
is the graph destroys it … A pass that cannot answer that must refuse, never
sweep."*

So the delete path is armed only by a census verdict in ``{"PLAN", "CLEAN"}`` **and**
a licensed population (gate 2) **and** an explicit per-node ``True`` from the record
probe (gate 3). A licence exists only where a parity instrument both RAN and was CLEAN
over the ENTIRE population (``ParityReport.proves()``).

⛔ ``CLEAN`` IS ADMITTED AND THAT IS NOT A LOOSENING OF THE ORDERING CONSTRAINT. The
census's ``verdict`` is its **BYTE** state (``resident_bytes > bound_bytes``); this
module enforces an **ENTRY** budget, and the two are not the same question — a cache far
over its node budget can sit under its byte ceiling (measured: 1,000,000 nodes against a
declared 20,000-node budget -> ``CLEAN``). Gating the licence on the byte verdict made
the enforced bound unreachable in exactly the population shape #10976 describes. Every
gate below is unchanged, and ``REFUSED``/``UNBOUNDED`` still yield an EMPTY licence.

⚠️ Record readiness is NOT why the licence is empty, and this header said it was:
``#10593`` (the record's write path) closed COMPLETED on 2026-11-06, so the record CAN
serve a node; only ``#7879`` (the epic) remains open, which is about the record as system
of record, not its ability to serve one. What withholds the licence is the absence of a
CLEAN parity census for the graph being swept — nothing schedules that instrument, so no
graph has one until an operator runs the census. In production the LICENCE is therefore
EMPTY (``REFUSED`` over the byte ceiling, ``CLEAN`` under it, with
``plan["evictable"] == []`` either way) and this module issues no mutating statement at
all.

⚠️ Note WHICH gate refuses: **gate 2** (the licence is empty because no population is
proven record-served), NOT gate 1. Gate 1 admits ``CLEAN`` on purpose — a production
cache under its byte ceiling answers ``CLEAN`` — so reading *"production can only ever be
REFUSED"* would attribute the refusal to the verdict word and make a licence widening
look harmless. The day a CLEAN parity census exists for the graph being swept, the licence
populates and this executor becomes reachable without a second decision — record readiness
is not the trigger, and this header said it was until #11709 corrected it.

⛔ SIX INDEPENDENT GATES, and each one alone refuses
-----------------------------------------------------
1. **The census** — the verdict must be in the allow-list ``{"PLAN", "CLEAN"}``.
   ⛔ An ALLOW-LIST, not "not refused": ``refused`` is True only for
   ``REFUSED``/``UNBOUNDED``, so a deny-list would arm the delete path for a verdict this
   module has never heard of (a typo, a future census verdict) whenever the caller also
   supplied a populated plan. ``CLEAN`` is admitted because it is the census's BYTE
   state and this module enforces an ENTRY budget — not because a byte-clean cache is
   "nothing to do". **It licenses nothing on its own:** gate 2 reads the licence from
   ``plan["evictable"]``, and ``REFUSED``/``UNBOUNDED`` still publish an EMPTY one.
2. **The licence and its SIZE** — the population must be named in
   ``plan["evictable"]``, and the census's own ``nodes`` count for it is a hard cap on
   how many may go. ⚠️ The cap bounds the NUMBER, not the membership: whether a
   candidate really belongs to the proven population is the SELECTOR's contract —
   this module knows no label's schema and cannot verify a population's shape
   (`turn_parity_census.py` owns the turn predicate, #3551) — so a mislabelled
   selection is caught only by gate 3, per node. The cap is what stops a bad selector
   from sweeping more than the instrument measured; it is not a membership proof.
3. **The node** — every candidate is asked of a ``record_probe`` immediately before
   it is deleted. A node the record cannot serve is *never* deleted, even when its
   population is licensed.
4. **The identity** — the delete matches ``id(n)`` **and** the record key. FalkorDB
   REUSES an internal node id after a delete (measured on the lane container
   ``ids [0,1,2]`` → delete 1 → create → the new node took id ``1``), so a delete
   keyed on the id alone would take a node created inside the probe→delete window
   that no probe ever saw. ``key_prop`` names the property the key lives in, and a
   candidate with no key cannot be deleted at all.
5. **The graph** — the handle being deleted from must NAME the graph the census
   measured (``Assessment.state.graph_name``). The binding is required one hop
   upstream (``load_parity_report(graph_name=…)``, #11097) because *"a CLEAN census of
   graph A licensed evicting graph B"*; this module is the half that DELETES, so it
   re-checks the binding at the last hop instead of trusting the caller to have kept
   the assessment and the handle together. A handle that names no graph, or a
   different one, is a REFUSAL.
6. **The pin** (#14012) — a candidate the caller's ``pinned`` predicate reports as
   resident is NEVER deleted, whatever the selector offered. Like the record probe
   this is a SEAM, not a policy: what is pinned belongs to the user's declaration
   (``tortoise/resident_pin.py``), and this module only asks. ⛔ It is checked BEFORE
   the lease and BEFORE the record probe, because a pin is the one answer the score
   may not overrule, and a node reported as "pinned" must not be reported as held for
   a reader or for an unservable record instead. A predicate that RAISES holds the
   node back: *could not ask* and *not pinned* are opposite findings, and the safe
   direction is the one that does not delete.

DRY RUN IS THE DEFAULT. ``dry_run=True`` returns the plan — which nodes, in which
order, and which were held back and why — and emits **no** mutating statement. It
keeps planning across passes until the budget is covered or the selector is
exhausted, so ``planned`` is the whole plan and not just its first batch. Executing
requires ``dry_run=False`` explicitly.

⛔ THE BOUND IS AN ENTRY BUDGET, AND THAT IS A MEASUREMENT, NOT A PREFERENCE
--------------------------------------------------------------------------
``TORTOISE_CACHE_MAX_NODES`` is the bound this executor enforces. ``TORTOISE_CACHE_EVICT_BATCH``
caps how many nodes one pass may take (default :data:`DEFAULT_BATCH`).

A *byte* ceiling is NOT the enforced unit, and the reason is measured rather than
argued. On a lane container (falkordb 4.20.6), creating 20,000 ``:Point`` nodes raised
``INFO memory used_memory`` by 3,113,176 B (~156 B/node); deleting HALF of them
returned 656,912 B (~66 B/node), and deleting ALL of them returned only 1,368,808 B —
**44% of what was added**. The rest is index memory and allocator retention that no
node deletion returns. So a byte ceiling is a *lagging* signal: a pass can delete
every cold node and still report over it, and a sweeper keyed on it would either spin
or empty the graph.

Two consequences, both load-bearing:

- **Entries are the enforceable, deterministic unit** — the standard bounded-cache
  shape (Redis's own client-side-caching reference: *"Client-local caches should have
  their own memory limit and eviction mechanism"*). Bounding entries is what makes
  "the cache stays bounded under N writes" true.
- **Bytes are the REPORTING unit.** ``used_memory`` before/after and
  ``bytes_reclaimed`` are in the metric, and so is whether the census's byte ceiling
  is still exceeded afterwards — because the index share (~48% of the graph per
  ``GRAPH.MEMORY USAGE``) is not evictable by deleting nodes at all. That half belongs
  to #5090, and the metric says so instead of implying node eviction fixed it.

⚠️ ``used_memory`` is the whole INSTANCE — the only exact figure the engine gives.
``GRAPH.MEMORY USAGE`` is a sampling estimate that omits per-key overhead and is blind
to the HNSW vector index (#5331), so it must never be read as a tenant's EXACT bytes.
(The allowance gate uses it only as a one-directional LOWER BOUND — warn at the cap,
hard-refuse at 1.5x — because every exclusion removes bytes; #13084.)

THE METRIC
----------
:meth:`EvictionReport.metric` is the documented, flat, stable-key surface: nodes
before/after, bytes before/after, planned/evicted, whether the degraded snapshot was
invalidated (``None`` = not applicable, ``False`` = needed and failed), and the two
hold-back reasons (record-cannot-serve, leased). A value nothing measured is ``None``,
never a fabricated ``0``.

IN-FLIGHT READS
---------------
:class:`ReadLeases` is the seam a reader registers in. A leased node is excluded
from the plan, and the registry is snapshotted AGAIN immediately before the delete
statement, so a read that starts during the probe is honoured.

⚠️ The guarantee is exactly that snapshot plus this module's own check, and no more: a
lease acquired AFTER the snapshot, while the statement is in flight, is not seen
(holding the registry lock across a network round-trip would serialise every reader
behind every delete, which is worse than the window). It is also **opt-in** — no read
site calls `lease()` yet, so a reader that does not register is not protected. Both
limits are stated here rather than implied.

THE OTHER CACHE THIS DELETE MUST INVALIDATE (#13123)
----------------------------------------------------
A node removed here is not only gone from FalkorDB: ``fallback_snapshot`` keeps a 60 s
in-memory copy of the whole ``:Point`` corpus and serves it from the DEGRADED tier —
the in-memory TF-IDF path that answers when the retrieval strategies fail. That copy is
invalidated by ``SDK._mark_dirty``, which every normal write surface calls and which a
raw ``DETACH DELETE`` does not. Deleting without invalidating it therefore reports
success while the node it removed is still served, which the M2 instrument measured as
``recall@10`` **0.1133** against ``resident_share`` **0.1100** — more nodes retrievable
than the graph held.

So :func:`_delete_nodes` calls ``fallback_snapshot.invalidate_graph(graph_name)``
AFTER its statement returns — on every route that removed rows, and also when the
statement RAISED (its effect is then unverified) — and ``evict()`` reports the outcome
as ``degraded_snapshot_invalidated`` (``None`` = nothing removed, so nothing to
invalidate; ``True`` = the stamp moved; ``False`` = needed and FAILED, which ``reason``
says in words on BOTH exits). The stamp is per-GRAPH, not per-key and not the whole-store
epoch: the evictor has a proven graph name and no projection, and a global stamp is the
measured ``#10602`` regression (20 builds, 0 hits).

⚠️ What it closes, and what it does not. It closes the CACHED entry and the PUT of any
build that read the graph before the delete. It does not stop a read that is already in
flight: a ``get`` landing between the delete committing and the stamp move, or a reader
that joined a build started before the stamp moved, is still served that flight's rows —
bounded by one round trip / one build, never by the TTL. Best-effort in the same posture
as the write path's COMMIT→INVALIDATE WINDOW, one statement wide instead of one TTL wide.

⚠️ The bound that remains: the snapshot store is PROCESS-LOCAL. An evictor in another
process (``tools/cache_evict.py`` run by an operator against a hosted API) cannot reach
the serving process's snapshot, and that store is still bounded only by the 60 s TTL.
The sweeper that arms the window (#13103) must run in the process that serves degraded
reads, or accept that bound.
"""
from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any

from tortoise.cache_eviction import Assessment, node_total

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_BATCH",
    "EVICTION_BATCH_ENV",
    "EVICTION_MAX_NODES_ENV",
    "Candidate",
    "EvictionConfigError",
    "EvictionPolicy",
    "EvictionReport",
    "ReadLeases",
    "evict",
]

#: The sentence a report carries when an eviction's degraded-snapshot invalidation
#: FAILED. One constant because it must appear on BOTH exits from a deleting pass — the
#: normal return AND the abort return (a delete statement that raised after earlier
#: passes deleted nodes). A failure reported in the metric but not in the prose is a
#: monitor-only signal, and the field's own contract says the reason says it in words.
_INVALIDATION_FAILED = (
    "; the degraded-fallback snapshot could NOT be invalidated, so a node this pass "
    "removed may still be served on the degraded read path (#13123) until the "
    "snapshot TTL expires"
)

#: The enforced bound: how many nodes the cache may hold.
#:
#: ⛔ ABSENT is a REFUSAL, never "unbounded". A cache with no declared bound is the
#: exact state #10976 was filed about, so this module will not invent a comfortable
#: default ceiling — that is a policy decision it has no authority to make.
EVICTION_MAX_NODES_ENV = "TORTOISE_CACHE_MAX_NODES"

#: How many nodes ONE pass may take. A cap, not a target — the runaway guard a
#: budget-less sweeper lacks. Deliberately modest: a pass is re-run while the cache is
#: over its bound, so taking fewer per pass is a slower response, never a wrong one.
EVICTION_BATCH_ENV = "TORTOISE_CACHE_EVICT_BATCH"
DEFAULT_BATCH = 500

#: Passes one :func:`evict` call may run. Each pass re-measures, so a bound that is
#: not reached in one batch is approached, never overshot.
DEFAULT_MAX_PASSES = 10


#: The shape a Cypher identifier may be emitted BARE — the census's own strict rule
#: (`cache_eviction.py`), restated here because `str.isidentifier()` also accepts a
#: trailing newline, which the engine reads as part of the name.
_BARE_SAFE = r"[A-Za-z_][A-Za-z0-9_]*"


class EvictionConfigError(RuntimeError):
    """A configuration value cannot be honoured — a REFUSAL, never a default.

    ⛔ The failure this exception exists to prevent is the quiet one: a mistyped bound
    that degrades to "unbounded" turns a bounded cache into the exact state #10976 was
    filed about. An unreadable bound must stop the pass, not widen it.
    """


@dataclass(frozen=True)
class EvictionPolicy:
    """The bound a pass enforces and the budget it may spend enforcing it."""

    max_nodes: int
    batch_size: int = DEFAULT_BATCH
    max_passes: int = DEFAULT_MAX_PASSES

    def __post_init__(self) -> None:
        # ⛔ Validated on the OBJECT, not only at construction from env: a policy built
        # directly (`EvictionPolicy(max_nodes=0)`) is the library door, and a zero bound
        # would authorise emptying the cache one batch at a time.
        for name, value, floor in (
            ("max_nodes", self.max_nodes, 1),
            ("batch_size", self.batch_size, 1),
            ("max_passes", self.max_passes, 1),
        ):
            if not isinstance(value, int) or isinstance(value, bool):
                raise EvictionConfigError(
                    f"{name} must be an integer, got {value!r}"
                )
            if value < floor:
                raise EvictionConfigError(
                    f"{name} {value} is below its floor of {floor} — a bound of zero "
                    f"evicts everything, which is never the intended reading"
                )

    @classmethod
    def resolve(
        cls,
        *,
        max_nodes: int | None = None,
        batch_size: int | None = None,
    ) -> EvictionPolicy:
        """Read the bound from the arguments, then the environment. Invalid ⇒ raise.

        Every branch either produces a positive integer or raises. There is no path
        that widens the bound, and none that silently disables eviction.
        """
        bound = max_nodes
        if bound is None:
            raw = os.environ.get(EVICTION_MAX_NODES_ENV)
            if raw is None or raw.strip() == "":
                raise EvictionConfigError(
                    f"no bound declared: set {EVICTION_MAX_NODES_ENV} (or pass "
                    f"max_nodes=) to a positive node count — a cache with no declared "
                    f"bound may not be swept (#10976: it holds every node forever)"
                )
            try:
                bound = int(raw)
            except ValueError as exc:
                raise EvictionConfigError(
                    f"{EVICTION_MAX_NODES_ENV}={raw!r} is not an integer node count"
                ) from exc

        batch = batch_size
        if batch is None:
            raw_batch = os.environ.get(EVICTION_BATCH_ENV)
            if raw_batch is None or raw_batch.strip() == "":
                batch = DEFAULT_BATCH
            else:
                try:
                    batch = int(raw_batch)
                except ValueError as exc:
                    raise EvictionConfigError(
                        f"{EVICTION_BATCH_ENV}={raw_batch!r} is not an integer node count"
                    ) from exc

        return cls(max_nodes=bound, batch_size=batch)


@dataclass(frozen=True)
class Candidate:
    """One node the selector offers for eviction, with its record key.

    ``key`` is the identity the RECORD knows the node by (``public.point.id``,
    ``public.source.url``, …) and is what ``record_probe`` is asked about. It is the
    selector's job to supply it: this module does not know any label's schema, and a
    guess would ask the record about the wrong row.

    ⛔ A candidate with ``key is None`` is UNDELETABLE. The delete binds on the key as
    well as the internal id (FalkorDB reuses ids), so a keyless candidate cannot be
    matched safely — it is held back rather than deleted on the id alone.
    """

    node_id: int
    key: str | None
    population: str

    def as_dict(self) -> dict[str, Any]:
        return {"node_id": self.node_id, "key": self.key, "population": self.population}


class ReadLeases:
    """The seam a reader registers in, so a REGISTERED read's node is held back.

    A plain in-process registry: ``with leases.lease(node_id): …`` around a read, and
    the executor skips any node the registry holds — both when planning and again
    immediately before the delete statement. Cross-process readers need a shared
    store; that is a deployment question, not a shape change, and the executor only
    ever asks the registry, never the transport.
    """

    def __init__(self) -> None:
        self._held: dict[int, int] = {}
        self._lock = Lock()

    @contextmanager
    def lease(self, node_id: int) -> Iterator[None]:
        self.acquire(node_id)
        try:
            yield
        finally:
            self.release(node_id)

    def acquire(self, node_id: int) -> None:
        with self._lock:
            self._held[node_id] = self._held.get(node_id, 0) + 1

    def release(self, node_id: int) -> None:
        with self._lock:
            remaining = self._held.get(node_id, 0) - 1
            if remaining <= 0:
                self._held.pop(node_id, None)
            else:
                self._held[node_id] = remaining

    def held(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._held)


Selector = Callable[[Any, int, str], Sequence[Candidate]]
RecordProbe = Callable[[Candidate], bool]


@dataclass
class EvictionReport:
    """What one :func:`evict` call decided, did, and refused — the metric's home.

    ``verdict`` values:

    ``REFUSED``      nothing was deleted and nothing may be — the reason is in
                     ``reason`` (census refused, population not licensed, config
                     invalid, or the selection failed).
    ``CLEAN``        the cache is within its node budget; nothing needed eviction.
    ``DRY_RUN``      over the budget; ``planned`` is exactly what WOULD be deleted.
    ``EVICTED``      ``deleted`` was removed and the cache was re-measured after.
    ``PARTIAL``      ``deleted`` was removed but the budget was not reached within the
                     pass budget — re-run, the population did not have enough nodes.
    """

    verdict: str
    reason: str = ""
    policy: EvictionPolicy | None = None
    licensed_populations: tuple[str, ...] = ()
    planned: tuple[Candidate, ...] = ()
    deleted: tuple[Candidate, ...] = ()
    skipped_record_cannot_serve: tuple[Candidate, ...] = ()
    skipped_leased: tuple[Candidate, ...] = ()
    #: #14012 — nodes held back because the caller's ``pinned`` predicate reported them
    #: resident. Its own list, not folded into either neighbours: a pinned node is held
    #: by the USER's declaration, and reporting it as "the record cannot serve it" or
    #: "a reader holds it" would send the next operator to widen a population or drain a
    #: lease — neither of which is the answer (unpin it, or raise the pin budget).
    skipped_pinned: tuple[Candidate, ...] = ()
    #: #14012 — nodes held back because the PIN PREDICATE COULD NOT ANSWER (it raised),
    #: which is a different fact from "the user pinned this" and gets a different
    #: remedy. ⛔ Folding the two into ``skipped_pinned`` told an operator to "unpin some
    #: of them, or raise TORTOISE_PIN_MAX_NODES" for nodes that were never established as
    #: pinned — an instruction that cannot clear the state, i.e. the report asserting a
    #: fact it does not have. The delete DECISION is unchanged (both hold the node back);
    #: only what the report claims is split.
    skipped_pin_unanswerable: tuple[Candidate, ...] = ()
    #: ``None`` — not ``0`` — where nothing was measured. A monitor that reads a
    #: missing measurement as a healthy zero is the defect this seam exists to close.
    nodes_before: int | None = None
    nodes_after: int | None = None
    bytes_before: int | None = None
    bytes_after: int | None = None
    byte_ceiling_bytes: int | None = None
    #: True when the delete statement removed fewer rows than it was asked to (a
    #: concurrent writer/evictor won the race). The metric must not claim otherwise.
    delete_unverified: bool = False
    #: #13123 — whether the degraded-fallback corpus snapshot was invalidated after
    #: nodes were removed. Three states, and the third is why this is not a bool:
    #:
    #: ``True``   nodes were removed AND the snapshot for that graph was dropped;
    #: ``None``   nothing was removed, so nothing needed invalidating (a CLEAN pass, a
    #:            dry run, or a refusal);
    #: ``False``  nodes were removed and the invalidation FAILED — an evicted node may
    #:            still be served on the degraded read path until the snapshot TTL.
    #:            That is a false PASS of this eviction, so ``reason`` says it in words
    #:            too; a monitor must not read ``False`` as "nothing to do".
    degraded_snapshot_invalidated: bool | None = None
    #: True when a DRY RUN ran out of pass budget before reaching the node budget, so
    #: ``planned`` under-reports the real plan. A truncated plan is not a complete one.
    truncated: bool = False
    #: True when the PASS budget (``max_passes``) ran out while still over the node
    #: budget. A caller must not read that as "the population had nothing to give" —
    #: the two states need different responses (re-run with a bigger budget vs. widen
    #: the proven population), and the metric has to tell them apart.
    budget_exhausted: bool = False
    dry_run: bool = True
    passes: int = 0

    def metric(self) -> dict[str, Any]:
        """Flat, stable keys — the surface a monitor or dashboard reads."""
        over_byte_ceiling = None
        if self.byte_ceiling_bytes is not None and self.bytes_after is not None:
            over_byte_ceiling = self.bytes_after > self.byte_ceiling_bytes
        reclaimed = None
        if self.bytes_before is not None and self.bytes_after is not None:
            reclaimed = self.bytes_before - self.bytes_after
        return {
            "issue": 10976,
            "verdict": self.verdict,
            "dry_run": self.dry_run,
            "nodes_before": self.nodes_before,
            "nodes_after": self.nodes_after,
            "nodes_evicted": len(self.deleted),
            "nodes_planned": len(self.planned),
            "max_nodes": self.policy.max_nodes if self.policy else None,
            "batch_size": self.policy.batch_size if self.policy else None,
            "bytes_before": self.bytes_before,
            "bytes_after": self.bytes_after,
            "bytes_reclaimed": reclaimed,
            "byte_ceiling_bytes": self.byte_ceiling_bytes,
            # ⛔ True here does NOT mean this pass failed. The index share of a graph is
            # not evictable by deleting nodes (#5090) — this field says the node budget
            # alone cannot satisfy the byte ceiling, which is a different finding.
            "byte_ceiling_still_exceeded": over_byte_ceiling,
            "held_record_cannot_serve": len(self.skipped_record_cannot_serve),
            "held_leased": len(self.skipped_leased),
            "held_pinned": len(self.skipped_pinned),
            "held_pin_unanswerable": len(self.skipped_pin_unanswerable),
            "delete_unverified": self.delete_unverified,
            # ⛔ `None` = NOT APPLICABLE, `False` = NEEDED AND FAILED. Collapsing the
            # two would let a failed invalidation read as a clean pass.
            "degraded_snapshot_invalidated": self.degraded_snapshot_invalidated,
            "plan_truncated": self.truncated,
            "pass_budget_exhausted": self.budget_exhausted,
            "max_passes": self.policy.max_passes if self.policy else None,
            "passes": self.passes,
            "reason": self.reason,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric(),
            "licensed_populations": list(self.licensed_populations),
            "planned": [c.as_dict() for c in self.planned],
            "deleted": [c.as_dict() for c in self.deleted],
            "skipped_record_cannot_serve": [
                c.as_dict() for c in self.skipped_record_cannot_serve
            ],
            "skipped_leased": [c.as_dict() for c in self.skipped_leased],
            "skipped_pinned": [c.as_dict() for c in self.skipped_pinned],
            "skipped_pin_unanswerable": [
                c.as_dict() for c in self.skipped_pin_unanswerable
            ],
        }


def _resident_bytes(client: Any) -> int | None:
    """``used_memory``, or ``None`` when it cannot be read. Never a fabricated 0."""
    try:
        value = client.info("memory").get("used_memory")
        # ⛔ `int()` is INSIDE the try: a client that formats its reading (`"12.5M"`)
        # must yield None — the documented "could not read" — not a ValueError thrown
        # out of the middle of a sweep.
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
    except Exception:  # pragma: no cover - transport-specific
        return None


def _key_property(key_prop: str) -> str:
    """*key_prop* as a Cypher property name, or REFUSE.

    ⛔ Same identifier discipline the census applies to labels
    (`tortoise/graph_key_restore.py::_quote_identifier` — imported, never
    re-implemented): a property name reaching a DELETE statement by interpolation
    comes from a caller, and one that is neither a strict identifier nor already
    quoted cannot be addressed safely.
    """
    from tortoise.graph_key_restore import _quote_identifier

    # ⛔ ONE backtick layer is stripped before quoting. `_quote_identifier` quotes a
    # non-bare name, so handing it an ALREADY-quoted `` `id` `` produces
    # ``` ```id``` ``` — which falkordb 4.20.6 rejects with `Invalid input '`'` in the
    # middle of a sweep. A caller quoting defensively is doing the right thing and
    # must not be punished for it.
    raw = key_prop.strip() if isinstance(key_prop, str) else ""
    if len(raw) >= 2 and raw.startswith("`") and raw.endswith("`"):
        raw = raw[1:-1].replace("``", "`")
    if not raw:
        raise EvictionConfigError(
            f"key property {key_prop!r} names no property — refusing to interpolate "
            f"an empty identifier into a DELETE"
        )
    quoted = _quote_identifier(raw)
    # The census's strict-full-match rule, for the same reason: `isidentifier()` also
    # accepts a trailing newline, which the engine reads as part of the name.
    if not quoted.startswith("`") and not re.fullmatch(_BARE_SAFE, quoted):
        raise EvictionConfigError(
            f"key property {key_prop!r} is not a safe Cypher identifier "
            f"(shared quoting returned it bare as {quoted!r}) — refusing rather than "
            f"interpolating it into a DELETE"
        )
    return quoted


def _invalidate_degraded_snapshot(graph_name: Any) -> bool:
    """Drop the degraded-fallback corpus snapshot for the graph just deleted from.

    ⛔ WHY THIS LIVES ON THE DELETE PATH AND NOT ON ITS CALLERS (#13123).
    ``fallback_snapshot`` keeps a 60 s in-memory copy of the whole corpus and serves it
    from the DEGRADED tier — the in-memory TF-IDF path that answers when the retrieval
    strategies fail. It is invalidated by ``SDK._mark_dirty``, which every normal write
    surface calls and which a raw ``DETACH DELETE`` does NOT. So a delete here left the
    node returnable, measured by the M2 instrument as ``recall@10`` **0.1133** against
    ``resident_share`` **0.1100** — more nodes retrievable than the graph held.

    That is a FALSE PASS of the eviction (it reports success while the node it removed
    is still served), and it is worst exactly when it matters: the degraded tier is
    active only when retrieval is already failing. Putting the invalidation in
    :func:`_delete_nodes` rather than at each caller means every delete route inherits
    it, including one added later.

    NEVER RAISES. This is cache bookkeeping running inside a sweep whose report is the
    only record of what was deleted: a raise here would replace a truthful report with
    a traceback and leave the caller unable to tell whether nodes went. A failure is
    logged and returned as ``False`` so the metric can say so.

    Returns ``True`` only when the invalidation actually ran. A non-string graph name
    (a stub handle with no ``name``) returns ``False`` — the honest reading, since a
    snapshot keyed on something else cannot be shown to have been reached.
    """
    if not isinstance(graph_name, str) or not graph_name:
        return False
    try:
        from tortoise.fallback_snapshot import invalidate_graph

        invalidate_graph(graph_name)
        return True
    except Exception as exc:  # noqa: BLE001, RUF100 - bookkeeping must never raise
        logger.warning(
            "#13123: the degraded-fallback snapshot for graph %r could NOT be "
            "invalidated after an eviction (%s: %s) — a node this pass removed may "
            "stay servable on the degraded read path for up to the snapshot TTL",
            graph_name, type(exc).__name__, exc,
        )
        return False


def _delete_nodes(
    graph: Any,
    rows: Sequence[Candidate],
    key_prop: str,
    *,
    snapshot_graph: str | None = None,
    snapshot_out: dict[str, bool | None] | None = None,
) -> tuple[int, set[int], bool | None]:
    """Delete the candidates, matching ``id(n)`` **and** the record key.

    ⛔ BOTH halves of the predicate are load-bearing. FalkorDB REUSES an internal node
    id once the node is gone (measured: ``ids [0,1,2]`` → delete ``1`` → create → the
    new node took id ``1``), so a delete keyed on the id alone can take a node created
    inside the probe→delete window that no probe ever saw. Binding on the record key
    closes that window: a recycled id whose key differs does not match.

    ``DETACH`` because a cache node's incident edges are part of the same cache entry
    — leaving dangling edges behind would be a partially-evicted node.

    ⛔ THE DEGRADED SNAPSHOT IS INVALIDATED HERE, AFTER THE STATEMENT RETURNS (#13123).
    Invalidation must come AFTER the delete, not before: a build that reads rows while
    the node is still resident would cache it, and only a stamp moved after the deletion
    refuses that build's put. It runs on every route that actually removed rows, and
    ALSO when the statement RAISED — an errored statement's effect is unverified and may
    be partial. A statement that returned ``count(n) = 0`` removed nothing, so it
    invalidates nothing: it cannot have introduced staleness, and blanking a warm
    snapshot on it would be a rebuild for no reason.

    ⚠️ THE WINDOW THIS DOES NOT CLOSE, stated here rather than left to the reader
    (review round 1, and the same class the write path documents at length under
    COMMIT→INVALIDATE WINDOW). Between the server COMMITTING the delete and this
    function's stamp move there is one round trip, and during it a degraded read can
    still be served the deleted node from the (still valid) cached entry — and a reader
    that joined a build started BEFORE the stamp moved is served that flight's result,
    which is bounded by the flight's duration, not by this stamp. So the guarantee is
    exactly: no snapshot is *cached* from a corpus read before the delete, and no cached
    entry survives the statement — never "no reader can observe the node". Best-effort
    in the same posture as the write path, one statement wide instead of one TTL wide.

    ``snapshot_graph`` is the graph name the caller has already PROVEN (``evict``'s
    gate 5 makes the handle's name equal the census's). It is not defaulted from
    ``graph.name`` on the production path: a name that was never checked against the
    licence is exactly the kind of coincidence this module refuses elsewhere. A direct
    caller that passes nothing gets the handle's own name, or no invalidation if the
    handle has none.

    Returns ``(removed_count, survivor_ids, snapshot_invalidated)`` so the caller
    reports what it can PROVE it removed rather than what it asked to remove, and can
    tell "the snapshot was invalidated" from "nothing needed invalidating"
    (``None``) and from "it was needed and FAILED" (``False``).

    ⛔ ``snapshot_out`` CARRIES THE OUTCOME ACROSS A RAISE. Without it the abort path
    cannot report the invalidation it performed (or failed to perform): the tuple never
    reaches the caller, so the report would say ``None`` — "not applicable" — about a
    statement whose effect is explicitly unverified. A failed invalidation on a raising
    statement is then indistinguishable from a failed invalidation on a successful one:
    both are ``False``.

    ⛔ THE MAPPING IS FILLED ON EVERY ROUTE THAT RUNS A STATEMENT — including the raising
    one — but NOT on the empty-payload return at the top, where no statement is issued and
    nothing is filled. A caller must read it with ``.get(...)`` and must not treat an empty
    mapping as an answer (``evict`` guards with ``snapshot_outcome.get(...) is not None``,
    which is correct for both reasons: absent and ``None`` both mean "no answer here").
    """
    payload = [{"id": c.node_id, "key": c.key} for c in rows if c.key is not None]
    if not payload:
        return 0, set(), None
    quote = _key_property(key_prop)
    target = snapshot_graph if isinstance(snapshot_graph, str) and snapshot_graph else \
        getattr(graph, "name", None)

    def _invalidate(needed: bool) -> bool | None:
        """Invalidate (when *needed*), publish the outcome, return it."""
        outcome = _invalidate_degraded_snapshot(target) if needed else None
        if snapshot_out is not None:
            snapshot_out["invalidated"] = outcome
        return outcome

    try:
        result = graph.query(
            f"UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{quote} = r.key "
            f"DETACH DELETE n RETURN count(n)",
            {"rows": payload},
        )
    except Exception:
        # ⛔ The statement was ISSUED and its effect is unverified, so the snapshot may
        # now name a node that is gone. Invalidate before the caller turns this into a
        # `delete_unverified` report and re-raises nothing.
        _invalidate(True)
        raise
    removed = int(result.result_set[0][0]) if result.result_set else 0
    # ⛔ `removed` from the STATEMENT. A statement that removed nothing introduced no
    # staleness, and a pass that skipped eviction must not blank a warm snapshot for
    # nothing (`None` = not needed).
    invalidated = _invalidate(bool(removed))
    survivors: set[int] = set()
    if removed != len(payload):
        # One extra read, only on the divergent path: name exactly which candidates
        # are still resident so the metric cannot over-report.
        still = graph.query(
            f"UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{quote} = r.key "
            f"RETURN id(n)",
            {"rows": payload},
        ).result_set
        survivors = {int(row[0]) for row in still}
    return removed, survivors, invalidated


def evict(
    assessment: Assessment,
    client: Any,
    graph: Any,
    *,
    population: str,
    selector: Selector,
    record_probe: RecordProbe,
    key_prop: str = "id",
    leases: ReadLeases | None = None,
    pinned: Callable[[Candidate], bool] | None = None,
    dry_run: bool = True,
    policy: EvictionPolicy | None = None,
) -> EvictionReport:
    """Enforce the node budget on **one** licensed population. Refuses by default.

    *selector* is ``(graph, limit, population) -> [Candidate]``, ordered cold-first by
    whatever policy the caller owns. This module deliberately does not invent a
    coldness rule: #10976 says *"What 'cold' should mean (settled by measurement, not
    here)"*, so the ordering is a required input, not a defaulted guess.

    *record_probe* is ``Candidate -> bool``: can the record serve this node? Asked per
    node, immediately before deletion, and a ``False`` holds the node back. An
    exception from the probe is a refusal for that node — the safe direction, since
    "could not ask" and "can serve" are opposite findings.

    *key_prop* is the node property holding ``Candidate.key`` (``id`` for ``:Point``).
    It is interpolated into the DELETE, so it is validated as a Cypher identifier.

    *pinned* (#14012) is ``Candidate -> bool``: is this node inside the user's
    *keep this resident* declaration (its 1-hop closure included)? Checked FIRST, so a
    pinned node is never deleted whatever the score said. ``None`` means the caller has
    no pin set — which is a different statement from an empty one, and this module does
    not choose it: reading the declaration, refusing an over-budget set and building the
    gate are ``tortoise/resident_pin.py``'s, and a sweeper that must not proceed without
    one refuses at its own door.
    """
    # ⛔ THE LICENCE IS A MAP, NOT A SET. The census's `nodes` count is the SIZED proof
    # ("this many nodes of this population are record-served"); discarding it would let
    # a selector offer more nodes than the instrument measured and still pass. A
    # non-string population is NOT stringified into a licence named "None".
    licensed: dict[str, int] = {}
    for entry in assessment.plan.get("evictable", []):
        if not isinstance(entry, dict):
            continue
        name = entry.get("population")
        if not isinstance(name, str):
            continue
        try:
            count = int(entry.get("nodes") or 0)
        except (TypeError, ValueError):
            count = 0
        # ⛔ MIN, NOT LAST-WINS. A plan carrying two entries for the same population
        # (a hand-built plan, or two censuses folded together) must TIGHTEN to the
        # smaller proven count. Overwriting would let the second entry widen a licence
        # the first had already bounded — a fail-open aggregate in the one field that
        # IS the licence.
        proven = max(0, count)
        licensed[name] = (
            proven if name not in licensed else min(licensed[name], proven)
        )
    ceiling = assessment.state.bound_bytes

    def _refuse(
        reason: str, *, nodes: int | None = None, nbytes: int | None = None
    ) -> EvictionReport:
        return EvictionReport(
            verdict="REFUSED",
            reason=reason,
            licensed_populations=tuple(sorted(licensed)),
            nodes_before=nodes,
            nodes_after=nodes,
            bytes_before=nbytes,
            bytes_after=nbytes,
            byte_ceiling_bytes=ceiling,
            dry_run=dry_run,
        )

    # ⛔ GATE 1 — the census. Checked BEFORE the policy is resolved, so a refused
    # census never even reads the environment: a pass that may not run must not depend
    # on configuration that could itself be broken. The census already measured the
    # resident bytes, so the report carries them rather than a fabricated 0.
    #
    # ⛔ ALLOW-LIST, NOT DENY-LIST. `assessment.refused` is True only for REFUSED and
    # UNBOUNDED, so "not refused" would ARM the DELETE path for a verdict this module
    # has never heard of (a typo, a future census verdict) whenever the caller also
    # supplied a populated plan. The licence is issued by exactly one verdict word.
    # ⛔ ALLOW-LIST, AND IT ADMITS TWO WORDS — `PLAN` AND `CLEAN`.
    #
    # The census's `verdict` is its BYTE state: `CLEAN` means "within the declared byte
    # ceiling", NOT "there is nothing to do". This pass enforces an ENTRY budget, and
    # the two units are not the same question — a cache 50x over its node budget but
    # under its byte ceiling answers `CLEAN` (measured: 1,000,000 nodes against a
    # declared budget of 20,000). Refusing on `CLEAN` made the enforced bound
    # unreachable in exactly the population shape #10976 describes (many small turn
    # nodes, which is why the byte figure lags the node count).
    #
    # ⛔ THIS WIDENS NOTHING. The licence is what gates the sweep and it is read from
    # `plan["evictable"]` by gate 2 below, unchanged; `REFUSED` and `UNBOUNDED` still
    # yield an EMPTY `evictable`, and every other word — a typo, a future census verdict
    # this module has never heard of — still refuses, because this is an allow-list.
    if assessment.verdict not in ("PLAN", "CLEAN"):
        return _refuse(
            f"census verdict {assessment.verdict!r} — only a PLAN or a CLEAN census "
            f"can license a sweep, and neither licenses one without a proven "
            f"population. The record is not proven to serve, so nothing may go "
            f"(#10976: 'A pass that cannot answer that must refuse, never sweep')",
            nbytes=assessment.state.resident_bytes,
        )

    # ⛔ GATE 2 — the licence. A population the census did not prove is not evictable,
    # whatever the caller asks for.
    if population not in licensed:
        return _refuse(
            f"population {population!r} is not licensed by the census plan "
            f"(evictable: {sorted(licensed) or 'none'}) — an unproven population may "
            f"not be swept"
        )

    # ⛔ GATE 5 — THE GRAPH. The licence is scoped to a graph, so the handle this pass
    # will delete from must be THAT graph. Checked before anything is measured, because
    # every reading below (the node total, the resident bytes) and the licence itself
    # are assertions about the graph the census saw.
    #
    # ⚠️ A handle with no readable `name` is REFUSED, not trusted. A duck-typed stub is
    # exactly the handle most likely to be pointed at the wrong graph, and "I cannot
    # tell" is not "it matches" — the same reason `_read_statement` refuses a handle
    # without `ro_query` instead of falling back to the creating verb.
    measured_graph = assessment.state.graph_name
    actual_graph = getattr(graph, "name", None)
    if not isinstance(measured_graph, str) or not measured_graph:
        return _refuse(
            "the census did not bind a graph name, so this pass cannot prove its "
            "licence applies to any graph — a cache node may only be evicted from the "
            "graph the instrument measured"
        )
    if not isinstance(actual_graph, str) or not actual_graph:
        return _refuse(
            f"the graph handle does not name its graph (got {actual_graph!r}), so it "
            f"cannot be proven to be the graph the census measured "
            f"({measured_graph!r}) — a delete needs a proven target, not an assumed one"
        )
    if actual_graph != measured_graph:
        return _refuse(
            f"this assessment is a licence for graph {measured_graph!r} but the handle "
            f"is graph {actual_graph!r} — a census of one graph may not evict another "
            f"(#10976: the record proof is earned per graph, not globally)"
        )

    # ⛔ AN UNREADABLE STORE IS A REFUSAL, NOT A TRACEBACK. This module's contract is
    # "never raise" for a store fault (`_resident_bytes` below already returns None
    # rather than a fabricated 0 for exactly this reason), so `node_total` — the one
    # call here that reaches the engine — must be guarded the same way. Unguarded it
    # raised `ConnectionError`/`CacheReadError` straight through the library door, which
    # is what a future sweeper consumes.
    try:
        before_nodes = node_total(graph)
    except Exception as exc:  # any store fault refuses the pass
        return _refuse(
            f"the resident node count could not be read "
            f"({type(exc).__name__}: {exc}) — an unreadable cache cannot be measured, "
            f"so nothing may be swept"
        )
    before_bytes = _resident_bytes(client)

    try:
        resolved = policy if policy is not None else EvictionPolicy.resolve()
        # ⛔ isinstance, not duck-typing. `__post_init__`'s floors are what stop a bound
        # of zero from emptying the cache one batch at a time, and an object that merely
        # LOOKS like a policy (`SimpleNamespace(max_nodes=0, …)`) skips them entirely.
        # Inside a try so the object/type mismatch is this module's REFUSAL, not a
        # TypeError escaping the sweep.
        if not isinstance(resolved, EvictionPolicy):
            raise EvictionConfigError(
                f"policy must be an EvictionPolicy, got {type(resolved).__name__} — an "
                f"object that only resembles one skips the bound validation the bounds"
                f" depend on"
            )
        quote = _key_property(key_prop)
    except EvictionConfigError as exc:
        return _refuse(
            f"config refused: {exc}", nodes=before_nodes, nbytes=before_bytes
        )
    del quote  # validated here; `_delete_nodes` re-derives it from `key_prop`

    leases = leases if leases is not None else ReadLeases()

    if before_nodes <= resolved.max_nodes:
        return EvictionReport(
            verdict="CLEAN",
            reason=f"{before_nodes} node(s) <= budget {resolved.max_nodes}",
            policy=resolved,
            licensed_populations=tuple(sorted(licensed)),
            nodes_before=before_nodes,
            nodes_after=before_nodes,
            bytes_before=before_bytes,
            bytes_after=before_bytes,
            byte_ceiling_bytes=ceiling,
            dry_run=dry_run,
        )

    # ⛔ THE PROVEN-POPULATION CAP. Evicting more nodes than the instrument measured is
    # evicting nodes nothing proved, however they were labelled. Cumulative across
    # passes, because the licence is per population, not per pass.
    licence_remaining = licensed[population]

    planned: list[Candidate] = []
    deleted: list[Candidate] = []
    held_record: list[Candidate] = []
    held_leased: list[Candidate] = []
    #: #14012 — the user's declaration, held back before anything else looks at the node.
    held_pinned: list[Candidate] = []
    #: Held back by a predicate that RAISED — held, but not established as pinned.
    held_pinned_unanswerable: list[Candidate] = []
    held_pinned_ids: set[int] = set()
    #: Ids already decided, so a later pass does not re-offer them. ⛔ Not a `seen`
    #: filter that also swallows HELD-BACK nodes: a node the record cannot serve sits
    #: at the HEAD of a coldest-first ordering forever, so a selector window of exactly
    #: `need` returns it every pass and the pass STARVES (a defect this suite caught:
    #: 3 of 4 evictions made, PARTIAL, cache wedged over its budget). The window is
    #: widened by the number already held instead.
    settled: set[int] = set()
    held_record_ids: set[int] = set()
    held_leased_ids: set[int] = set()
    passes = 0
    after_nodes = before_nodes
    after_bytes = before_bytes
    delete_unverified = False
    #: #13123 — `None` until a statement actually removes a node, so a CLEAN pass, a dry
    #: run and a refusal all report "not applicable" rather than a fabricated `True`.
    degraded_snapshot_invalidated: bool | None = None
    abort_reason: str | None = None
    #: WHY the loop stopped — "satisfied" | "population" | "licence" | "pass_budget".
    #: ⛔ Reported, not inferred: "ran out of passes" and "the population had nothing
    #: left" need different responses, and a reason derived from `passes == max_passes`
    #: alone claims the first when the last allowed pass had already proven the second.
    stop_reason = "pass_budget"
    #: False when a post-delete re-measure failed. The after-state is then UNMEASURED
    #: and must be reported as ``None``, not as the pre-delete values.
    after_measured = True
    #: ⛔ A SEPARATE flag from `after_measured`: the node count and the byte reading are
    #: two different reads, and either can fail alone. Reusing one flag for both would
    #: null out a node count that WAS measured — regressing the fix this pairs with.
    bytes_measured = before_bytes is not None
    #: The selector window, carried across passes so it can GROW (see below). Starts at
    #: 0; the first pass sets it from `budget` alone.
    window = 0
    #: ⛔ CARRIED OUT OF THE LOOP, because a pass that ends with its whole window held
    #: back at the DELETE STATEMENT does not end the loop — it widens and tries again. When
    #: `max_passes` then runs out, `stop_reason` is still its `"pass_budget"` initial value,
    #: and that arm tells the operator to RE-RUN — which cannot converge: the window restarts
    #: at `budget`, the selector is deterministic, and the same nodes are held at the same
    #: asks. The actionable reply (`unpin …`, or wait for the reader) lives in the pin/lease
    #: arms, so the diagnosis is carried here and applied after the loop.
    hold_reason: str | None = None

    for _ in range(resolved.max_passes):
        over = after_nodes - resolved.max_nodes
        if over <= 0:
            stop_reason = "satisfied"
            break
        remaining_plan = over - len(planned) if dry_run else over
        budget = min(resolved.batch_size, remaining_plan, licence_remaining)
        if budget <= 0:
            stop_reason = "licence" if licence_remaining <= 0 else "population"
            break
        passes += 1
        # ⛔ MULTIPLICATIVE, not additive. Growing by `batch` per pass means the
        # examined prefix after P passes is only `P x batch`; doubling makes the widest
        # prefix ONE CALL can ever examine `batch_size x 2^(max_passes-1)`.
        #
        # ⛔ THE REAL BOUND, stated because an earlier version of this comment claimed
        # "doubling reaches any head within `log2(nodes)` passes" and that is FALSE:
        # `window` starts at `budget` on EVERY call (nothing survives a call), so at the
        # defaults (batch 500 x 10 passes) the widest prefix is 500 x 2^9 = 256,000
        # nodes. A 1M-node graph would need 12 passes, not 10, and an unservable cold
        # head longer than 256,000 nodes is never passed — by this call OR any later one.
        # Measured on the real `evict()`: 400,000 resident nodes, a 300,000-node
        # unservable cold head, `max_nodes=133,333` -> `PARTIAL`, `deleted=0`,
        # `passes=10`, `held=256,000`, and a reason that says "re-run to continue" — an
        # instruction that cannot succeed. It fails CLOSED (nothing unproven is deleted),
        # which is why this is a reporting defect and not a safety one; the stop reason
        # below now says which of the two it is. Carrying the window across calls is the
        # fix if the starvation is to be closed outright, and is not done here.
        window = min(
            after_nodes,
            max(
                window * 2,
                budget + len(settled) + len(held_record_ids) + len(held_leased_ids)
                + len(held_pinned_ids),
            ),
        )
        try:
            offered = list(selector(graph, window, population))
        except Exception as exc:
            abort_reason = (
                f"selector failed ({type(exc).__name__}: {exc}) — an unreadable "
                f"selection is a refusal, not an empty one"
            )
            break

        held_before = (len(held_record_ids), len(held_leased_ids), len(held_pinned_ids))
        # ⛔ A selection is a CLAIM this module verifies before believing it.
        local: list[Candidate] = []
        local_ids: set[int] = set()
        for candidate in offered:
            if len(local) >= budget:
                break
            # ⛔ DEDUPE BY NODE ID. A join-shaped selector can return the same node on
            # two rows, and FalkorDB's `RETURN count(n)` counts matched ROWS, not
            # distinct nodes (measured: 3 rows naming 2 nodes -> count 3, both gone).
            # A duplicate would therefore inflate `nodes_evicted` and spend the licence
            # twice for one node.
            if candidate.node_id in local_ids:
                continue
            if candidate.node_id in settled or candidate.node_id in held_leased_ids:
                continue
            if candidate.population != population:
                abort_reason = (
                    f"selector offered population {candidate.population!r} while "
                    f"sweeping {population!r} — a mixed selection may not be swept"
                )
                break
            # ⛔ GATE 6 — the PIN (#14012). Asked FIRST, because the score may not
            # overrule the user's own declaration: a pinned node is not "cold", it is
            # exempt. ⛔ The polarity is `bool(...)`, deliberately NOT gate 3's `is True`:
            # there, anything but an explicit True HOLDS the node, so a truthy non-bool
            # is safe; here the same spelling would DELETE it (`1`, `"pinned"`,
            # `numpy.True_` are all truthy and none is `True`). For a pin the safe
            # direction is the opposite one — anything truthy, or an unanswerable
            # question, holds the node back.
            if pinned is not None:
                answered = True
                try:
                    is_pinned = bool(pinned(candidate))
                except Exception:
                    # ⛔ COULD NOT ASK IS NOT "PINNED". Both hold the node back — that is
                    # the safe direction and it does not change — but they are different
                    # facts with different remedies, so they are reported apart:
                    # `unpin`/raise-the-budget cannot clear a predicate that raised.
                    is_pinned = True
                    answered = False
                if is_pinned:
                    if candidate.node_id not in held_pinned_ids:
                        held_pinned_ids.add(candidate.node_id)
                        seen_ids = held_pinned if answered else held_pinned_unanswerable
                        seen_ids.append(candidate)
                    continue
            if candidate.node_id in leases.held():
                held_leased_ids.add(candidate.node_id)
                held_leased.append(candidate)
                continue
            # ⛔ GATE 3 — the node. Asked immediately before deletion; an exception is
            # a refusal for that node.
            if candidate.key is None:
                servable = False
            else:
                try:
                    # ⛔ `is True`, not `bool(...)`. A probe returning a TRUTHY
                    # non-boolean — `"cannot serve: no row"`, `{"ok": False}`, or the
                    # very common `(ok, detail)` tuple, whose `bool()` is True even
                    # when `ok` is False — would read as "the record can serve" and the
                    # node would be deleted. #10976: a pass that cannot ANSWER must
                    # refuse, so anything other than an explicit True holds the node.
                    servable = record_probe(candidate) is True
                except Exception:
                    servable = False
            if not servable:
                if candidate.node_id not in held_record_ids:
                    held_record_ids.add(candidate.node_id)
                    held_record.append(candidate)
                continue
            # ⛔ A NODE THAT BECOMES SERVABLE LEAVES THE HELD LIST. Without this a
            # candidate held in pass N and reported servable in pass N+1 is DELETED
            # while still listed in `skipped_record_cannot_serve`, so `metric()`'s
            # `held_record_cannot_serve` over-reports and any monitor that intersects
            # `deleted` with `skipped_record_cannot_serve` (the probe's own
            # `unservable_evicted` pattern) raises a false alarm on the one invariant
            # that matters most.
            if candidate.node_id in held_record_ids:
                held_record_ids.discard(candidate.node_id)
                held_record[:] = [
                    c for c in held_record if c.node_id != candidate.node_id
                ]
            # ⛔ AND THE SAME PRUNE FOR THE PIN, which had none: `held_pinned_ids` only
            # prevented a duplicate APPEND, it was never consulted when the node was
            # taken — so a node whose pin predicate RAISED once (a store blip) and
            # answered False on the retry reached `local` and was DELETED while staying
            # in `skipped_pin_unanswerable`. `deleted` then intersected
            # `skipped_pin_unanswerable`, which is precisely the "stale held entry" the
            # record-probe list above clears for its own, and the pin's invariant is the
            # stronger one ("a pinned node is never deleted").
            if candidate.node_id in held_pinned_ids:
                held_pinned_ids.discard(candidate.node_id)
                held_pinned[:] = [
                    c for c in held_pinned if c.node_id != candidate.node_id
                ]
                held_pinned_unanswerable[:] = [
                    c for c in held_pinned_unanswerable
                    if c.node_id != candidate.node_id
                ]
            local.append(candidate)
            local_ids.add(candidate.node_id)

        if abort_reason is not None:
            break
        if not local:
            # ⛔ Do not break just because THIS window was entirely held back. The
            # window widening (by `len(held_*)`) only helps on the NEXT pass, so
            # breaking here means it never applies in the case it was written for: a
            # cold set whose head is unservable starves at the first window and the
            # cache stays over budget forever, while the reason claims the population
            # "did not hold enough evictable nodes" — which is false.
            held_grew = (len(held_record_ids), len(held_leased_ids),
                         len(held_pinned_ids)) != held_before
            if window >= after_nodes or not held_grew:
                # ⛔ WHICH hold-back stopped this pass is REPORTED, not assumed. An
                # all-LEASED window is not an exhausted population: the nodes are there
                # and a reader is holding them, so "the population did not hold enough
                # evictable nodes" sends the next reader to widen a population that was
                # never the problem — and the real answer ("reads are holding these") is
                # in no population query. When BOTH hold-backs are present the RECORD is
                # the binding one (those nodes can never be taken), so it keeps
                # "population".
                # ⛔ #14012 ADDS A THIRD HOLD-BACK and it needs its own word for the same
                # reason: a window made entirely of PINNED nodes is not an exhausted
                # population either — the answer is "unpin, or raise the pin budget",
                # and neither is reachable from a message about widening a selector.
                # ⛔ AND THE PIN OUTRANKS THE LEASE IN THIS ARM TOO — the same ordering as
                # gate 6 itself and as the last-hop arm below, because `unpin` is the only
                # remedy that clears a pin while a lease clears itself.
                if held_record_ids:
                    stop_reason = "population"
                elif held_pinned_unanswerable:
                    stop_reason = "pin-unanswerable"
                elif held_pinned_ids:
                    stop_reason = "pinned"
                elif held_leased_ids:
                    stop_reason = "leased"
                else:
                    stop_reason = "population"
                break
            # The window grew but this pass found nothing new to take: keep widening.
            continue
        settled.update(local_ids)
        licence_remaining -= len(local)

        if dry_run:
            planned.extend(local)
            continue

        # ⛔ GATE 4's second half — a lease taken DURING the probe must still win. The
        # plan-time check is not enough: a read can start between planning and the
        # statement, which is exactly the window a lease exists to close.
        #
        # ⛔ AND GATE 6 GETS THE SAME SECOND HALF, FOR THE SAME WINDOW. The header's promise
        # ("a pinned node is NEVER deleted, whatever the selector offered") is the STRONGER
        # one, so it cannot be the weaker one that goes un-re-asked: a `pin` declaration
        # written between planning and the DELETE used to let those nodes be deleted AND
        # reported in `deleted`, so the report could not even show that it happened. The
        # polarity is gate 6's own — anything truthy holds, and a predicate that RAISES
        # holds too (unanswerable is not "unpinned"), reported apart because the remedies
        # differ.
        holds = leases.held()
        final: list[Candidate] = []
        late_pinned = 0
        late_pinned_unanswerable = 0
        for candidate in local:
            # ⛔ THE PIN IS ASKED BEFORE THE LEASE HERE TOO, so all three sites agree
            # (plan-time gate 6, this loop, and both stop-reason arms below). The ordering
            # is not cosmetic: the buckets exist to route the operator to the remedy that
            # clears the state, `unpin` is the only one that clears a pin, and the module
            # header's rule is that a node reported as "pinned" must not be reported as
            # held for a reader instead.
            if pinned is not None:
                answered = True
                try:
                    is_pinned = bool(pinned(candidate))
                except Exception:
                    is_pinned = True
                    answered = False
                if is_pinned:
                    if candidate.node_id not in held_pinned_ids:
                        held_pinned_ids.add(candidate.node_id)
                        seen_ids = held_pinned if answered else held_pinned_unanswerable
                        seen_ids.append(candidate)
                    if answered:
                        late_pinned += 1
                    else:
                        late_pinned_unanswerable += 1
                    continue
            if candidate.node_id in holds:
                if candidate.node_id not in held_leased_ids:
                    held_leased_ids.add(candidate.node_id)
                    held_leased.append(candidate)
                continue
            final.append(candidate)

        if not final:
            # ⛔ REFUND THE LICENCE FIRST. `local` was debited above and this arm takes
            # nothing, so without the refund the next pass computes
            # `budget = min(batch, remaining_plan, licence_remaining) = 0` and stops with
            # the FALSE reason `licence` — a debit that no delete explains.
            licence_remaining += len(local)
            # ⛔ AND DO NOT BREAK JUST BECAUSE THIS WINDOW WAS ENTIRELY HELD BACK — the
            # same rule the `if not local:` arm above states, for the same reason. The
            # window widens on the NEXT pass (it is recomputed from the held sets each
            # pass), so breaking here means the widening never applies in the case it was
            # written for: a pin that becomes visible only at the delete statement would
            # abandon the remaining passes and report zero evictions while the identical
            # declaration, seen one ask earlier, would have delivered them. Break only when
            # there is nothing left to widen (`window >= after_nodes`); `max_passes` is the
            # loop's own bound, so this cannot spin. ⛔ There is deliberately NO
            # "did the hold sets grow" disjunct: every node in `local` was provably absent
            # from both sets (the plan-time gate discards stale entries immediately before
            # adding it), so the last-hop loop always adds each of them to exactly one
            # bucket and the counts always differ — a disjunct that can never be false is a
            # guard that guards nothing, which is worse than no guard because it reads as
            # one.
            if window >= after_nodes:
                # ⛔ WHICH hold-back stopped this pass is REPORTED, not assumed. The PIN
                # arms come first: the pin is the control a reader can clear themselves
                # (`unpin`), whereas a lease is a reader that was live at statement time,
                # and the same ordering governs the `if not local:` arm above so the two
                # cannot disagree about the same both-holds condition.
                if late_pinned_unanswerable:
                    stop_reason = "pin-unanswerable"
                elif late_pinned:
                    stop_reason = "pinned"
                else:
                    stop_reason = "leased"
                break
            # The window is not yet the whole graph: widen and try the next pass, carrying
            # the diagnosis in case this is the last pass the budget allows.
            hold_reason = (
                "pin-unanswerable" if late_pinned_unanswerable
                else "pinned" if late_pinned
                else "leased"
            )
            continue
        # A pass that reached the delete statement is taking nodes, so an earlier pass's
        # hold no longer explains why the sweep stopped.
        hold_reason = None
        # ⛔ `snapshot_out` IS HOW THE ABORT PATH LEARNS WHAT THE INVALIDATION DID. On a
        # raise the tuple below never arrives, so without this channel the report would
        # have to say `None` — "not applicable" — about a statement whose effect is
        # explicitly UNVERIFIED. That is the one reading this field must never produce:
        # `None` means "nothing was removed, so nothing needed invalidating", and a
        # raising delete may well have removed rows.
        snapshot_outcome: dict[str, bool | None] = {}
        try:
            removed, survivors, snapshot_invalidated = _delete_nodes(
                graph, final, key_prop, snapshot_graph=measured_graph,
                snapshot_out=snapshot_outcome,
            )
        except Exception as exc:  # any store fault aborts, never raises
            # ⛔ THE PASS MAY ALREADY HAVE DELETED NODES. Unguarded, a fault here
            # escaped `evict()` after earlier passes had committed deletes, so the
            # caller lost the entire report — no `deleted`, no `nodes_after` — and the
            # CLI turned it into a traceback (exit 1 = the USAGE code). This is the
            # same class as the post-delete re-measure guard, one statement earlier.
            # The abort path returns the record (`nodes_after=None`: UNMEASURED,
            # never a fabricated value), naming the deletions already made.
            delete_unverified = True
            after_measured = False
            bytes_measured = False
            # ⛔ GUARD ON THE VALUE, NOT THE DICT (review round 2, P1 — measured).
            # `_invalidate(needed=False)` deliberately publishes `None` ("nothing removed,
            # so nothing needed invalidating"). Guarding on the dict alone therefore let a
            # LATER pass that removed 0 rows blank what an EARLIER pass proved: measured
            # pass 1 deleting 2 rows with a FAILED invalidation, pass 2 removing 0 and its
            # re-count raising → report said `deleted=2`, `nodes_evicted=2`,
            # `degraded_snapshot_invalidated=None`, and no "could NOT be invalidated"
            # sentence — a failed invalidation erased from the machine-readable surface on
            # the very exit this channel was added for. `None` means "needed nothing", so
            # it must never overwrite a real answer; this mirrors the normal path's
            # `if snapshot_invalidated is not None:`.
            if snapshot_outcome.get("invalidated") is not None:
                degraded_snapshot_invalidated = snapshot_outcome["invalidated"]
            # ⛔ `deleted` counts EARLIER passes at this point — this pass's own rows
            # are not in it yet. Saying "after {len(deleted)} node(s) were already
            # removed by this pass" understated the removal (0 on pass 1) while the
            # statement had in fact been ASKED for `len(final)` rows.
            abort_reason = (
                f"the delete statement raised ({type(exc).__name__}: {exc}) while "
                f"asked to remove {len(final)} node(s); {len(deleted)} node(s) from "
                f"EARLIER passes were already removed and this statement's effect is "
                f"UNVERIFIED — the after-state is UNMEASURED"
            )
            break
        if removed != len(final):
            delete_unverified = True
        # ⛔ RECORD the invalidation outcome rather than assume it. `True` proves it ran;
        # `False` means a node this pass removed may still be served on the degraded
        # path, which the reason states so the metric and the prose agree.
        if snapshot_invalidated is not None:
            degraded_snapshot_invalidated = snapshot_invalidated
        # ⛔ Count from the STATEMENT's own number. `survivors` is the complement of a
        # predicate query, so a row a peer evicted inside the probe→delete window is
        # absent from BOTH sets — taking `final - survivors` would count it as ours,
        # over-report `nodes_evicted` and permanently burn a unit of the licence. When
        # the statement disagrees with the plan the IDENTITIES are not recoverable, so
        # the count is truthful and the identity list is truncated to it.
        actually_deleted = [c for c in final if c.node_id not in survivors][:removed]
        deleted.extend(actually_deleted)
        # ⛔ REFUND THE LICENCE for anything the pass decided not to take. The charge is
        # taken before the delete (a dry run needs it), so rows dropped by the lease
        # re-check or by a divergent survivor count would otherwise burn part of the
        # population's proof for good — and a later pass would report PARTIAL while the
        # cache is still over budget.
        licence_remaining += len(local) - len(actually_deleted)
        # ⛔ The re-measure is GUARDED. An unguarded failure here escapes `evict` AFTER
        # nodes were removed, so no report is returned and the only record of what left
        # the cache is lost — the exact class the abort path exists to prevent, reached
        # on the most ordinary failure there is (a transport blip).
        try:
            after_nodes = node_total(graph)
        except Exception as exc:
            abort_reason = (
                f"the cache could not be re-measured after {len(deleted)} node(s) were "
                f"evicted ({type(exc).__name__}: {exc}) — the deletions above are "
                f"recorded, but the remaining state is UNKNOWN"
            )
            after_measured = False
            break
        measured = _resident_bytes(client)
        if measured is None:
            # ⛔ `_resident_bytes` returns None on failure by contract; dropping it
            # leaves `after_bytes` at the PRE-DELETE reading, which the report would
            # then hand back as `bytes_reclaimed: 0` beside `deleted=4` — a fabricated
            # metric about a state nobody measured.
            bytes_measured = False
        else:
            after_bytes = measured
            bytes_measured = True

        # ⛔ An honest stop: no progress means the bound cannot be reached by this
        # population, and spinning would be a busy-loop dressed as thoroughness.
        if removed == 0:
            stop_reason = "population"
            break

    # ⛔ CARRY THE HOLD DIAGNOSIS OUT OF THE LOOP. The last pass may have been entirely
    # held back at the delete statement and then run out of `max_passes`, leaving
    # `stop_reason` at its initial `"pass_budget"`. That arm advises a RE-RUN, which cannot
    # converge here (the window does not survive the call and the selector is deterministic,
    # so the same nodes are held at the same asks) — while the answer the operator needs
    # ("unpin these", "wait for those readers") is in the arms this mapping selects.
    if stop_reason == "pass_budget" and hold_reason is not None:
        stop_reason = hold_reason

    # ⛔ A refusal reached AFTER an earlier pass already deleted something must not
    # report "nothing was deleted" — the metric would be false and the only record of
    # what left the cache would be lost.
    if abort_reason is not None:
        # ⛔ A truncated DRY RUN that aborts must still say its plan is incomplete;
        # otherwise a consumer reading `plan_truncated is False` takes the partial plan
        # for the whole one, and the verdict word is the only thing contradicting it.
        abort_over = after_nodes - resolved.max_nodes
        suffix = (
            f" — {len(deleted)} node(s) were evicted by pass(es) of this call"
            if deleted
            else ""
        )
        # ⛔ The invalidation clause is NOT conditional on the exit path. A pass that
        # deleted nodes and then aborted still owes the reader "and the degraded tier may
        # still serve them"; leaving it off here would report `False` in the metric
        # beside a reason that never mentions it.
        if degraded_snapshot_invalidated is False:
            suffix += _INVALIDATION_FAILED
        return EvictionReport(
            verdict="REFUSED",
            reason=abort_reason + suffix,
            policy=resolved,
            licensed_populations=tuple(sorted(licensed)),
            planned=tuple(planned),
            deleted=tuple(deleted),
            skipped_record_cannot_serve=tuple(held_record),
            skipped_leased=tuple(held_leased),
            skipped_pinned=tuple(held_pinned),
            skipped_pin_unanswerable=tuple(held_pinned_unanswerable),
            nodes_before=before_nodes,
            # ⛔ UNMEASURED is None, never the pre-delete reading. Reporting
            # `nodes_after=before_nodes` beside `deleted=4` is a fabricated metric: a
            # monitor reads "4 evicted, 0 bytes reclaimed, still over budget" about a
            # pass whose state nobody measured.
            nodes_after=after_nodes if after_measured else None,
            bytes_before=before_bytes,
            bytes_after=after_bytes if (after_measured and bytes_measured) else None,
            byte_ceiling_bytes=ceiling,
            delete_unverified=delete_unverified,
            degraded_snapshot_invalidated=degraded_snapshot_invalidated,
            truncated=dry_run and abort_over > len(planned),
            dry_run=dry_run,
            passes=passes,
        )

    over_after = after_nodes - resolved.max_nodes
    verdict = "DRY_RUN" if dry_run else "EVICTED"
    reason = (
        f"dry run over budget {resolved.max_nodes}: {len(planned)} node(s) would be "
        f"evicted"
        if dry_run
        else f"evicted {len(deleted)} node(s) toward budget {resolved.max_nodes}"
    )
    # ⛔ A plan cut short is NOT a complete plan. With the defaults (batch 500 x 10
    # passes) a 20k-node graph's plan would under-report 4x and still read as done.
    truncated = dry_run and over_after > len(planned)
    if dry_run and truncated:
        short = over_after - len(planned)
        if stop_reason == "licence":
            cause = (
                f"the census proved only {licensed[population]} node(s) for this "
                f"population — widen the proven population to go further"
            )
        elif stop_reason == "population":
            cause = (
                f"the selector had no more record-servable nodes to offer "
                f"({len(held_record)} held back because the record cannot serve them)"
            )
        elif stop_reason == "leased":
            cause = (
                f"{len(held_leased)} node(s) are held by an in-flight READ, so the "
                f"population is not exhausted"
            )
        elif stop_reason == "pin-unanswerable":
            cause = (
                f"the pin predicate could not answer for "
                f"{len(held_pinned_unanswerable)} node(s) "
                f"(`skipped_pin_unanswerable`), so they were held — nothing is "
                f"established about them; repair the pin set and re-run"
            )
        elif stop_reason == "pinned":
            cause = (
                f"{len(held_pinned)} node(s) are PINNED (`skipped_pinned`) — the "
                f"selector is offering nodes the user declared resident, so the "
                f"population is not exhausted; unpin some of them or raise the pin budget"
            )
        else:
            cause = "this pass budget could plan no more"
        reason += f"; TRUNCATED — {short} more node(s) over the budget: {cause}"
    # ⛔ "Ran out of passes" and "the population had nothing left" are DIFFERENT states
    # with different responses (re-run with a bigger budget vs. widen the proven
    # population). Reporting the second when the first is true sends the next reader
    # after a population that is not the problem.
    # A DRY RUN can be cut by the pass budget too; the field asks "is the pass budget
    # the binding constraint?", which is true there as well. `truncated` is what says
    # the plan is short, so the two agree.
    budget_exhausted = (
        over_after > 0
        and stop_reason == "pass_budget"
        and (not dry_run or truncated)
    )
    if not dry_run and over_after > 0:
        # Not a failure of the pass — it hit its budget — but the caller must not read
        # a still-over-budget cache as done.
        verdict = "PARTIAL"
        if budget_exhausted:
            # ⛔ "RE-RUN TO CONTINUE" IS ONLY TRUE WHEN THE HEAD IS SHORTER THAN ONE
            # CALL CAN EXAMINE. The widest prefix a single call reaches is
            # `batch_size x 2^(max_passes-1)`, and the window does NOT survive the call,
            # so a held-back head at or beyond that bound is never passed — telling the
            # operator to re-run would send them into a loop that cannot converge.
            reach = resolved.batch_size * (2 ** (resolved.max_passes - 1))
            # ⛔ THE PIN IS A THIRD HOLD-BACK and it was missing here, so an all-pinned
            # head was told "re-run to continue" — an instruction that examines the same
            # nodes and stops in the same place, forever, while the only working reply
            # ("unpin, or raise the pin budget") lives in the `pinned` arm this path
            # never reaches. Reproduced: 6 pinned nodes, batch 1 x 3 passes (reach 4),
            # `held_pinned=4`, `deleted=0`, reason "re-run to continue".
            if (len(held_record) >= reach or len(held_leased) >= reach
                    or len(held_pinned) >= reach
                    or len(held_pinned_unanswerable) >= reach):
                reason += (
                    f"; still {over_after} node(s) over the budget — the held-back head "
                    f"({len(held_record)} unservable, {len(held_leased)} leased, "
                    f"{len(held_pinned)} pinned, "
                    f"{len(held_pinned_unanswerable)} pin-unanswerable) has "
                    f"reached the widest prefix ONE call can examine "
                    f"({reach} = batch {resolved.batch_size} x "
                    f"2^(max_passes-1 {resolved.max_passes - 1})); re-running will not "
                    f"converge — "
                    # ⛔ THE REMEDY IS CHOSEN BY WHICH HOLD-BACK FILLED THE HEAD, because
                    # they need OPPOSITE advice: a PINNED node is released by `unpin` or a
                    # bigger budget, while a node whose pin predicate RAISED is released by
                    # neither — this file says so two arms below ("unpinning or raising the
                    # budget cannot clear this, because these nodes were never shown to be
                    # pinned"). Offering `unpin` for the second is the instruction that
                    # cannot be followed, again.
                    + (
                        "unpin some of them, or raise TORTOISE_PIN_MAX_NODES while "
                        "keeping it smaller than the window"
                        if len(held_pinned) >= reach
                        else "repair the pin set (its declaration, or the graph behind "
                             "it) and re-run — unpinning and raising the budget cannot "
                             "clear it, because these nodes were never shown to be pinned"
                        if len(held_pinned_unanswerable) >= reach
                        else "raise the batch or the pass budget, or widen the proven "
                             "population"
                    )
                )
            else:
                reason += (
                    f"; still {over_after} node(s) over the budget after the full "
                    f"{resolved.max_passes}-pass budget — re-run to continue (a single "
                    f"call examines at most {reach} node(s) ahead of the selection)"
                )
        elif stop_reason == "licence":
            reason += (
                f"; still {over_after} node(s) over the budget — the census proved "
                f"only {licensed[population]} node(s) for this population, so widen "
                f"the proven population to go further"
            )
        elif stop_reason == "leased":
            reason += (
                f"; still {over_after} node(s) over the budget — {len(held_leased)} "
                f"node(s) are held by an in-flight READ (`skipped_leased`), so the "
                f"population is not exhausted; re-run when those reads finish"
            )
        elif stop_reason == "pin-unanswerable":
            reason += (
                f"; still {over_after} node(s) over the budget — the pin predicate "
                f"could not answer for {len(held_pinned_unanswerable)} node(s) "
                f"(`skipped_pin_unanswerable`, #14012), so they were HELD rather than "
                f"taken: nothing is established about them. Repair the pin set (its "
                f"declaration or the graph) and re-run; unpinning or raising the budget "
                f"cannot clear this, because these nodes were never shown to be pinned"
            )
        elif stop_reason == "pinned":
            reason += (
                f"; still {over_after} node(s) over the budget — {len(held_pinned)} "
                f"node(s) are PINNED (`skipped_pinned`, #14012), so the population is "
                f"not exhausted: unpin some of them, or raise "
                f"TORTOISE_PIN_MAX_NODES while keeping it smaller than the window"
            )
        else:
            reason += (
                f"; still {over_after} node(s) over the budget — the selected "
                f"population did not hold enough evictable nodes"
            )
    if delete_unverified:
        reason += (
            "; the delete statement removed fewer rows than planned (a concurrent "
            "writer or evictor), so only the proven removals are counted"
        )
    # ⛔ A FAILED INVALIDATION IS A FALSE PASS, so it is stated in prose as well as in the
    # metric. The nodes ARE gone (that half succeeded); what this pass cannot claim is
    # that they stopped being served.
    if degraded_snapshot_invalidated is False:
        reason += _INVALIDATION_FAILED
    report = EvictionReport(
        verdict=verdict,
        reason=reason,
        policy=resolved,
        licensed_populations=tuple(sorted(licensed)),
        planned=tuple(planned),
        deleted=tuple(deleted),
        skipped_record_cannot_serve=tuple(held_record),
        skipped_leased=tuple(held_leased),
        skipped_pinned=tuple(held_pinned),
        skipped_pin_unanswerable=tuple(held_pinned_unanswerable),
        nodes_before=before_nodes,
        nodes_after=after_nodes,
        bytes_before=before_bytes,
        bytes_after=after_bytes if (after_measured and bytes_measured) else None,
        byte_ceiling_bytes=ceiling,
        delete_unverified=delete_unverified,
        degraded_snapshot_invalidated=degraded_snapshot_invalidated,
        truncated=truncated,
        budget_exhausted=budget_exhausted,
        dry_run=dry_run,
        passes=passes,
    )
    return report
