"""The read-path status vocabulary — roadmap §7 item 9 (ADOPTED 2026-09-17).

The LLM-free ``search`` / ``recall`` read path reports exactly one of four
recorded terms, so that **memory unavailable** can never be indistinguishable
from **memory empty**:

    available     the store was reached AND hits were returned
    empty         the store was reached AND nothing matched
    degraded      the store was reached, results were returned (or could be),
                  but a leg did not run — e.g. the embedder is not installed so
                  the dense leg was skipped (the #2573 / #2898 class), or
                  annotation/assembly failed
    unconfigured  the read path could not reach a store at all — no store /
                  endpoint configured, or unreachable — NEVER reported as empty

⛔ **THE VOCABULARY IS RECORDED — CONSUME IT, DO NOT MINT.** These four terms
are the adopted contract (roadmap §7 item 9). Do not add a fifth, do not rename
one, do not add a synonym. A state the four do not cover is a contract change:
raise it instead of naming one locally — coining a term in parallel is how one
contract becomes two.

**The client boundary is NOT mapped here.** ``scripts/tortoise-memory.mjs``
still exits 0 for both "unreachable" and "empty" (issue #3805); mapping the
client side to distinct exit codes is the client-boundary lane's work, not this
module's. The cross-lane acceptance test — the same condition must produce the
same term on both sides — is likewise a lane-orchestrator step and does not
exist yet. What this module guarantees is the read-path term itself.

**The load-bearing property:** ``unconfigured`` must never be indistinguishable
from ``empty``, and a failure must never be returned as a successful empty
result.

**Additive and off by default.** The status is computed only when a caller
passes ``read_status_out`` (the same caller-owned-mutable-sink pattern as the
private ``leg_trace``), and the hosted read surface emits the field only when
``TORTOISE_READ_STATUS`` is truthy (``1``/``true``/``yes``/``on``; unset or
``0`` means off). With the field off every response is byte-identical, so this
is neither a new tool nor a new endpoint — but it is still RECORDED
(``config/surface-manifest.yml`` → ``response_fields``, the #3863 rule).
"""
from __future__ import annotations

import os

#: The four recorded terms — the whole vocabulary, in no implied order.
STATUS_AVAILABLE = "available"
STATUS_EMPTY = "empty"
STATUS_DEGRADED = "degraded"
STATUS_UNCONFIGURED = "unconfigured"

READ_STATUSES: tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_EMPTY,
    STATUS_DEGRADED,
    STATUS_UNCONFIGURED,
)

_TRUTHY = frozenset({"1", "true", "yes", "on"})

#: Leg-trace reasons (the R3 #1542 D4 shape) that prove the store ANSWERED the
#: query. ``index_missing`` / ``no_embeddings`` still mean the server replied —
#: the index was absent, not the store. A leg that never answered
#: (``query_failed`` / ``breaker_open`` / ``timeout``) proves nothing about
#: reachability, so it cannot be read as "the store was reached".
_ANSWERED_REASONS = frozenset(
    {"ok", "empty_results", "index_missing", "no_embeddings"}
)


def read_status_enabled() -> bool:
    """True when the read surface may EMIT the status field (default off).

    Mirrors the W4 why-layer convention: only a truthy value
    (``1``/``true``/``yes``/``on``) turns it on; unset or ``0`` leaves every
    response byte-identical.
    """
    return os.environ.get("TORTOISE_READ_STATUS", "").strip().lower() in _TRUTHY


def classify_read_status(*, reached: bool, hit_count: int, degraded: bool) -> str:
    """Map the three observed facts onto the four recorded terms.

    ``degraded`` outranks ``empty``: when a leg did not run, "nothing matched"
    is not established, so the honest report is a degraded read rather than an
    empty one. Reachability outranks everything — nothing else can be claimed
    about a store that was never reached.
    """
    if not reached:
        return STATUS_UNCONFIGURED
    if degraded:
        return STATUS_DEGRADED
    return STATUS_AVAILABLE if hit_count > 0 else STATUS_EMPTY


def classify_leg_trace(entries, *, hit_count: int,
                       reached: bool | None = None) -> str:
    """Classify a read from its leg trace (R3 #1542 D4) and its hit count.

    ``reached`` is derived, never assumed: either rows came back, or at least
    one leg answered the store. ``degraded`` is true when any leg recorded a
    degradation (a leg skipped, an index missing, a timeout) or when the
    TF-IDF fallback actually produced the rows. A zero-count fallback is a
    recovery that found nothing, not a degradation — the #2952 rule, so an
    honest no-match read on a healthy store is ``empty`` and not ``degraded``.

    ``reached`` may be passed explicitly when the caller has INDEPENDENT proof
    the store answered — the read path proves it with a reachability probe, and
    a caller holding that proof must not have the answer walked back by a trace
    whose legs were skipped by a tripped circuit breaker: a breaker-open leg is
    a leg that did not run (``degraded``), never an unreachable store.
    """
    derived_reached = False
    degraded = False
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        if entry.get("leg") == "fallback":
            # The fallback is a RECOVERY path, not a leg of the read. It only
            # proves a degraded (keyword-only) result when it produced rows;
            # a zero-count fallback found nothing and disqualifies nothing.
            if (entry.get("count") or 0) > 0:
                derived_reached = True
                degraded = True
            continue
        if entry.get("degraded"):
            degraded = True
        if entry.get("ran") and entry.get("reason") in _ANSWERED_REASONS:
            derived_reached = True
    if hit_count > 0:
        derived_reached = True
    return classify_read_status(
        reached=(derived_reached if reached is None else reached),
        hit_count=hit_count,
        degraded=degraded,
    )


def combine_read_statuses(*statuses: str | None) -> str | None:
    """Coalesce the statuses of the calls that make up ONE composite read.

    A leg that returned hits, ran degraded, or answered-and-found-nothing
    proves the store WAS reached — so ``unconfigured`` is not available as the
    composite answer once any leg is one of those; a leg that could not be
    reached then means the composite read was incomplete (``degraded``), not
    that the store was absent. ``unconfigured`` wins only when NO leg reached
    the store at all. This keeps the composite payload self-consistent: a read
    that returns rows can never simultaneously report ``unconfigured``.
    """
    present = [status for status in statuses if status]
    if not present:
        return None
    reached = any(
        status in (STATUS_AVAILABLE, STATUS_DEGRADED, STATUS_EMPTY)
        for status in present
    )
    if not reached:
        return STATUS_UNCONFIGURED
    if STATUS_DEGRADED in present or STATUS_UNCONFIGURED in present:
        return STATUS_DEGRADED
    if STATUS_AVAILABLE in present:
        return STATUS_AVAILABLE
    return STATUS_EMPTY
