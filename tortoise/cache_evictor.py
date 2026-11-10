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
look harmless. The day the record can serve, the licence populates and this executor
becomes reachable without a second decision.

⛔ FIVE INDEPENDENT GATES, and each one alone refuses
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
to the HNSW vector index (#5331), so it must never be wired to a cap.

THE METRIC
----------
:meth:`EvictionReport.metric` is the documented, flat, stable-key surface: nodes
before/after, bytes before/after, planned/evicted, and the two hold-back reasons
(record-cannot-serve, leased). A value nothing measured is ``None``, never a
fabricated ``0``.

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
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from threading import Lock
from typing import Any

from tortoise.cache_eviction import Assessment, node_total

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
            "delete_unverified": self.delete_unverified,
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


def _delete_nodes(graph: Any, rows: Sequence[Candidate], key_prop: str) -> tuple[int, set[int]]:
    """Delete the candidates, matching ``id(n)`` **and** the record key.

    ⛔ BOTH halves of the predicate are load-bearing. FalkorDB REUSES an internal node
    id once the node is gone (measured: ``ids [0,1,2]`` → delete ``1`` → create → the
    new node took id ``1``), so a delete keyed on the id alone can take a node created
    inside the probe→delete window that no probe ever saw. Binding on the record key
    closes that window: a recycled id whose key differs does not match.

    ``DETACH`` because a cache node's incident edges are part of the same cache entry
    — leaving dangling edges behind would be a partially-evicted node.

    Returns ``(removed_count, survivor_ids)`` so the caller reports what it can PROVE
    it removed rather than what it asked to remove.
    """
    payload = [{"id": c.node_id, "key": c.key} for c in rows if c.key is not None]
    if not payload:
        return 0, set()
    quote = _key_property(key_prop)
    result = graph.query(
        f"UNWIND $rows AS r MATCH (n) WHERE id(n) = r.id AND n.{quote} = r.key "
        f"DETACH DELETE n RETURN count(n)",
        {"rows": payload},
    )
    removed = int(result.result_set[0][0]) if result.result_set else 0
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
    return removed, survivors


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
                budget + len(settled) + len(held_record_ids) + len(held_leased_ids),
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

        held_before = (len(held_record_ids), len(held_leased_ids))
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
            held_grew = (len(held_record_ids), len(held_leased_ids)) != held_before
            if window >= after_nodes or not held_grew:
                # ⛔ WHICH hold-back stopped this pass is REPORTED, not assumed. An
                # all-LEASED window is not an exhausted population: the nodes are there
                # and a reader is holding them, so "the population did not hold enough
                # evictable nodes" sends the next reader to widen a population that was
                # never the problem — and the real answer ("reads are holding these") is
                # in no population query. When BOTH hold-backs are present the RECORD is
                # the binding one (those nodes can never be taken), so it keeps
                # "population".
                stop_reason = (
                    "leased" if (held_leased_ids and not held_record_ids)
                    else "population"
                )
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
        holds = leases.held()
        final: list[Candidate] = []
        for candidate in local:
            if candidate.node_id in holds:
                if candidate.node_id not in held_leased_ids:
                    held_leased_ids.add(candidate.node_id)
                    held_leased.append(candidate)
                continue
            final.append(candidate)

        if not final:
            # ⛔ NOT "population". Every node this pass was about to take is held by a
            # READER (skipped_leased), so blaming the population sends the next reader
            # to research an exhausted selector that is not the problem — the same
            # misattribution class `stop_reason` exists to separate ("satisfied" |
            # "population" | "licence" | "pass_budget"), and worse here because the
            # answer is "a reader is holding these", which no population query reveals.
            stop_reason = "leased"
            break
        try:
            removed, survivors = _delete_nodes(graph, final, key_prop)
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
        return EvictionReport(
            verdict="REFUSED",
            reason=abort_reason + suffix,
            policy=resolved,
            licensed_populations=tuple(sorted(licensed)),
            planned=tuple(planned),
            deleted=tuple(deleted),
            skipped_record_cannot_serve=tuple(held_record),
            skipped_leased=tuple(held_leased),
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
            if len(held_record) >= reach or len(held_leased) >= reach:
                reason += (
                    f"; still {over_after} node(s) over the budget — the held-back head "
                    f"({len(held_record)} unservable, {len(held_leased)} leased) has "
                    f"reached the widest prefix ONE call can examine "
                    f"({reach} = batch {resolved.batch_size} x "
                    f"2^(max_passes-1 {resolved.max_passes - 1})); re-running will not "
                    f"converge — raise the batch or the pass budget, or widen the "
                    f"proven population"
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
    report = EvictionReport(
        verdict=verdict,
        reason=reason,
        policy=resolved,
        licensed_populations=tuple(sorted(licensed)),
        planned=tuple(planned),
        deleted=tuple(deleted),
        skipped_record_cannot_serve=tuple(held_record),
        skipped_leased=tuple(held_leased),
        nodes_before=before_nodes,
        nodes_after=after_nodes,
        bytes_before=before_bytes,
        bytes_after=after_bytes if (after_measured and bytes_measured) else None,
        byte_ceiling_bytes=ceiling,
        delete_unverified=delete_unverified,
        truncated=truncated,
        budget_exhausted=budget_exhausted,
        dry_run=dry_run,
        passes=passes,
    )
    return report
