"""Interface D — the RESIDENCY ROUTER: a cache miss is not an insufficiency (#7953).

WHY THIS MODULE EXISTS
======================
``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0 (owner, 2026-10-08) makes
**Postgres the system of record and FalkorDB a derived hot cache**. §12.3's tier
table then assumed each tier is COMPLETE; under the hybrid the cache is
**PARTIAL by design** — the limit is held high precisely so a miss is
*deferred*, not absent. §17.3 states the consequence and names the artifact this
module builds:

    "the evidence I hold does not answer this"   → escalate a tier   (§12.4)
    "this node is not in the cache"              → fetch from the truth store

Those are **two different events with two different repairs**. §12.4's
sufficiency router routes on **answer depth inside a tier that holds the node at
all**; this module is the **second, distinct router**, and it routes on
**CACHE RESIDENCY**. Collapsing them either escalates unnecessarily or — worse —
**treats a cache miss as a negative finding**, the one wrong answer that is
indistinguishable from a right one (§17.2).

THE DECISION, AND WHY IT IS A PURE FUNCTION
===========================================
This module DECIDES; it performs no I/O. Its input is §17.2's completeness
marker (``tortoise.residency``, the marker's ONE home, ``#7952``) plus the
sufficiency verdict §12.4 already produces; its output is ONE canonical route.
It never fetches the record and never walks a tier — the caller does, with the
repair this decision names. That keeps the decision unit-testable in isolation
and keeps the router distinct from the sufficiency router rather than a
re-implementation of it.

⛔ **RESIDENCY OUTRANKS SUFFICIENCY.** A node the cache cannot see makes every
sufficiency verdict undecidable: the evidence in hand is *necessarily*
incomplete, so *"insufficient"* has not been established — and walking to a
colder tier of the SAME store that never held the node cannot repair it. So a
residency miss is routed to the record **before** the sufficiency branch is
consulted. This ordering is the collapse guard, and it is pinned by test.

THE THREE ROUTES — a DIRECTIVE, never a status term
===================================================
``serve``           the cache's own answer stands; no repair is warranted.
``fetch-record``    a residency repair: fetch from the truth store (Postgres).
``escalate-tier``   a sufficiency repair: go deeper, as §12.4 already does.

⛔ **NOT a fifth term of the recorded status vocabulary** (§17.3). That
vocabulary — ``available | empty | degraded | unconfigured`` — is RECORDED,
ONE home, ``tortoise/status_vocabulary.py``, whose own text forbids a fifth
term, and it names *what happened* to a read. A route names **where to look**,
exactly as ``hot``/``not-hot`` does. The two vocabularies are deliberately
disjoint, and a test pins the disjointness.

THE FOURTH BRANCH — a cache that is DOWN
========================================
Test-design ``#7967`` S5 names a branch §17.3 does not: *"timeout / service down
/ network error on the cache"*. A down cache is a **residency miss**, never an
insufficiency and never an absence — a store that cannot be reached is a store
that cannot see. ``cache_reachable=False`` therefore routes to the record, with
its own ``reason`` so an outage is never confused with a declared ``not-hot``.
The marker's own home already keeps the two separable on the wire: an outage
leaves ``residency`` ABSENT and names the impairment ``degraded``.

AND THE CASE THE MARKER DID NOT RUN
===================================
``marker=None`` means **NOT ASSESSED** — the marker's flag is off (the
byte-identical default of §2.0, under which the cache is complete and the marker
CANNOT fire), or the store was never reachable (covered by
``cache_reachable=False``). It is *not* an assertion of ``hot``: the decision
records ``marker=None`` so a consumer can see residency was never established,
and it carries its OWN reason (``unassessed``) rather than ``resident`` — a
consumer keyed on ``reason`` must not read a residency verdict from a marker
that never ran. With no residency signal the router falls back to §12.4's
behaviour — the pre-existing route — which is what "byte-identical when the
marker is off" requires.

WHAT THIS MODULE DELIBERATELY IS NOT
====================================
* **Not the marker.** ``tortoise/residency.py`` is its ONE home (``#7952``);
  this module consumes it and mints no verdict.
* **Not the sufficiency router.** §12.4's router is its own artifact; this
  module routes to it (``escalate-tier``) and never re-implements the depth
  decision.
* **Not the record read path.** Fetching from the record is ``#7970``
  (deferred by §2.0); this module names the repair and carries the coordinate
  it must be fetched by.
* **No public surface change.** No MCP tool and no public ``TortoiseSDK``
  method is added, removed or renamed.

Owner scope ruling 2026-10-10 (``#7953``): there are no existing customers, so
no migration-safety apparatus appears here — nothing in this module is
justified by customer-migration safety.
"""
from __future__ import annotations

from dataclasses import dataclass

from .residency import (
    RESIDENCY_NOT_HOT,
    RESIDENCY_VERDICTS,
    ResidencyDeclaration,
    mark_residency,
)

__all__ = [
    "REASONS",
    "REASON_CACHE_UNREACHABLE",
    "REASON_INSUFFICIENCY",
    "REASON_RESIDENCY_MISS",
    "REASON_RESIDENT",
    "REASON_UNASSESSED",
    "ROUTES",
    "ROUTE_ESCALATE_TIER",
    "ROUTE_FETCH_RECORD",
    "ROUTE_SERVE",
    "ResidencyRoute",
    "route_for_coordinate",
    "route_residency",
]

#: The three canonical routes. A **directive about where to look**, never a
#: status of the read (§17.3) — disjoint from the recorded four-term vocabulary.
ROUTE_SERVE = "serve"
ROUTE_FETCH_RECORD = "fetch-record"
ROUTE_ESCALATE_TIER = "escalate-tier"
ROUTES: tuple[str, ...] = (ROUTE_SERVE, ROUTE_FETCH_RECORD, ROUTE_ESCALATE_TIER)

#: WHY the route fired — so the two events, and the outage, stay distinguishable
#: *at the point where the decision is made*.
REASON_RESIDENT = "resident"                  # the marker was hot — nothing to repair
REASON_RESIDENCY_MISS = "residency-miss"      # outside the known-hot set
REASON_CACHE_UNREACHABLE = "cache-unreachable"  # the store could not answer
REASON_INSUFFICIENCY = "insufficiency"        # resident, but the evidence is short
#: NOT ASSESSED is not "resident": the marker never ran, so there is no
#: residency claim to make. The route is the same (``serve``); the *reason*
#: must not read as a positive verdict a ``reason``-keyed consumer never got.
REASON_UNASSESSED = "unassessed"              # the marker was never read
REASONS: tuple[str, ...] = (
    REASON_RESIDENT, REASON_RESIDENCY_MISS,
    REASON_CACHE_UNREACHABLE, REASON_INSUFFICIENCY,
    REASON_UNASSESSED,
)

#: The sufficiency verdict is a three-state fact, not a boolean: `True` the
#: evidence answers; `False` it does not (escalate); `None` it was not measured
#: (never escalate on a fact nobody established).
_INSUFFICIENT = False


@dataclass(frozen=True)
class ResidencyRoute:
    """ONE routing decision, with the facts it was made from.

    ``route``        one of :data:`ROUTES`.
    ``reason``       one of :data:`REASONS` — which event fired.
    ``marker``       the completeness verdict read (``hot`` / ``not-hot``), or
                     ``None`` when residency was NOT ASSESSED. ``None`` is
                     deliberately not normalized to ``hot``: an unassessed
                     marker is not a claim that the node was resident.
    ``sufficiency``  the sufficiency verdict read (``True`` / ``False`` /
                     ``None``), carried unchanged.
    ``coordinate``   the ``label:key`` this read named, when it named one — what
                     a ``fetch-record`` repair must be fetched by. ``None`` for
                     a set read, which names no node.
    """

    route: str
    reason: str
    marker: str | None = None
    sufficiency: bool | None = None
    coordinate: str | None = None

    def __post_init__(self) -> None:
        if self.route not in ROUTES:
            raise ValueError(
                f"route must be one of {ROUTES}, got {self.route!r}")
        if self.reason not in REASONS:
            raise ValueError(
                f"reason must be one of {REASONS}, got {self.reason!r}")
        # The module's rule is that an unknown marker is REFUSED, and this type
        # is in ``__all__`` — so the TYPE must enforce it, not only the factory.
        # A directly-constructed decision must not carry an unvalidated verdict.
        if self.marker is not None and self.marker not in RESIDENCY_VERDICTS:
            raise ValueError(
                f"marker must be one of {RESIDENCY_VERDICTS} or None, got "
                f"{self.marker!r}")

    @property
    def serves(self) -> bool:
        return self.route == ROUTE_SERVE

    @property
    def fetches_record(self) -> bool:
        """The RESIDENCY repair — fetch from the truth store (§17.3)."""
        return self.route == ROUTE_FETCH_RECORD

    @property
    def escalates_tier(self) -> bool:
        """The SUFFICIENCY repair — go deeper (§12.4)."""
        return self.route == ROUTE_ESCALATE_TIER


def route_residency(*, marker: str | None, sufficient: bool | None = None,
                    cache_reachable: bool = True,
                    coordinate: str | None = None) -> ResidencyRoute:
    """Route ONE cache read: the two events, kept apart.

    ``marker``            §17.2's verdict for this read (``hot`` / ``not-hot``),
                          or ``None`` when residency was NOT ASSESSED.
    ``sufficient``        §12.4's verdict: does the evidence in hand answer?
    ``cache_reachable``   did the cache store answer AT ALL? ``False`` is an
                          outage, which is a residency miss (not an absence and
                          not an insufficiency).
    ``coordinate``        the ``label:key`` the read named, when it named one.

    Precedence — and the reason for it:

    1. **the cache did not answer** → ``fetch-record`` (``cache-unreachable``).
       A store that cannot be reached cannot see.
    2. **the marker says ``not-hot``** → ``fetch-record`` (``residency-miss``).
       §17.3: outside the known-hot set, the repair is the record — NEVER a tier
       escalation, and never an absence.
    3. **resident (or unassessed) and the evidence is short** → ``escalate-tier``
       (``insufficiency``) — §12.4's existing repair, reached only once
       residency has been established (or was never in question).
    4. otherwise → ``serve`` — ``resident`` when the marker is ``hot``, or
       ``unassessed`` when the marker is ``None``. NOT ASSESSED is not a
       residency claim, so it may not be reported as one; the route is the
       same either way.

    An unknown marker is REFUSED: a typo'd verdict must not silently take a
    branch, because a wrong route is a wrong answer.
    """
    if marker is not None and marker not in RESIDENCY_VERDICTS:
        raise ValueError(
            f"marker must be one of {RESIDENCY_VERDICTS} or None, got "
            f"{marker!r}")

    if not cache_reachable:
        return ResidencyRoute(
            route=ROUTE_FETCH_RECORD, reason=REASON_CACHE_UNREACHABLE,
            marker=marker, sufficiency=sufficient, coordinate=coordinate)

    if marker == RESIDENCY_NOT_HOT:
        return ResidencyRoute(
            route=ROUTE_FETCH_RECORD, reason=REASON_RESIDENCY_MISS,
            marker=marker, sufficiency=sufficient, coordinate=coordinate)

    if sufficient is _INSUFFICIENT:
        return ResidencyRoute(
            route=ROUTE_ESCALATE_TIER, reason=REASON_INSUFFICIENCY,
            marker=marker, sufficiency=sufficient, coordinate=coordinate)

    if marker is None:
        # Never ``resident``: the marker did not run, so no residency claim was
        # made. `.marker is None` already preserves the distinction; the reason
        # must name the truth too, or a `reason`-keyed consumer reads a claim.
        return ResidencyRoute(
            route=ROUTE_SERVE, reason=REASON_UNASSESSED,
            marker=marker, sufficiency=sufficient, coordinate=coordinate)

    return ResidencyRoute(
        route=ROUTE_SERVE, reason=REASON_RESIDENT,
        marker=marker, sufficiency=sufficient, coordinate=coordinate)


def route_for_coordinate(declaration: ResidencyDeclaration, *, label: str,
                         key: str, hits: int = 0, scope: str = "",
                         sufficient: bool | None = None,
                         cache_reachable: bool = True) -> ResidencyRoute:
    """Route a read that NAMED a node — the per-key entry (§17.1's coordinate).

    The coordinate is built by ``cache_projection.declared_key`` (``#7971``),
    the SAME function the cache projection declares hot keys with, so the writer
    and the reader speak one vocabulary instead of agreeing by accident. A bare
    key would collapse ``:Subject``/``:Object``, which share the ``name``
    namespace, and let a partial cache answer a miss with an absence.

    ⭐ **This is what makes a PARTIAL declaration route an UNWRITTEN key to the
    record.** A read of a covered key that returned nothing is a genuine
    in-store finding (``serve``); a read of a key the declaration does not
    enumerate is a residency miss (``fetch-record``) — never an absence.
    """
    if not key:
        raise ValueError("key must be a non-empty node key")
    if not label:
        raise ValueError("label must be a non-empty node label")
    # Lazy: keeps this module's import light and free of any projection cycle.
    from .cache_projection import declared_key

    coordinate = declared_key(label, key, scope=scope)
    marker = mark_residency(declaration, hits=hits, key=coordinate)
    return route_residency(
        marker=marker, sufficient=sufficient,
        cache_reachable=cache_reachable, coordinate=coordinate)
