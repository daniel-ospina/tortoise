"""The read-path half of the recorded status vocabulary — roadmap §7 item 9.

The LLM-free ``search`` / ``recall`` read path reports exactly ONE of the four
recorded terms, so that **memory unavailable** can never be indistinguishable
from **memory empty**:

    available      the store was reached AND hits were returned
    empty          the store was reached AND nothing matched
    degraded       the memory layer is IMPAIRED — the store is configured but
                   could not be reached (off by outage), or it answered while a
                   retrieval leg did not run (the #2573 / #2898 embedder-absent
                   class)
    unconfigured   NO store / endpoint is configured — "off by policy", a
                   set-up gap. NEVER reported as empty

⛔ **THE VOCABULARY IS RECORDED — CONSUME IT, DO NOT MINT.** The four terms and
the condition each names are declared in ONE home,
``tortoise/status_vocabulary.py`` (the client-boundary vocabulary, #3805 / PR
#4044). This module does not re-declare the words: ``STATUS_*`` and
``READ_STATUSES`` are re-exported from that home, and ``classify_read_status``
DELEGATES the configuration / reachability / content mapping to its
``classify``. Do not add a fifth term, rename one, add a synonym, or fork the
mapping locally — a term coined in parallel is how one contract becomes two.

**The one read-path dimension the four terms do not name.** A retrieval leg
that did not run (no embedder installed, a tripped breaker, a timeout) leaves
the store ANSWERED but the memory layer IMPAIRED. The four terms have no name
for "the store answered, but not every leg did", so that state is carried as
the classified status ``degraded`` — the recorded term for an impaired memory —
while the leg-by-leg detail stays in the existing ``leg_trace``. No fifth term
is invented for it.

**The load-bearing property:** ``empty`` (the store answered and had nothing)
is never reported as ``degraded`` or ``unconfigured`` (the store did not
answer), and neither failure is ever reported as a successful empty result.
``unconfigured`` (never declared) and ``degraded`` (configured but impaired)
are likewise never reported as each other.

**Where ``unconfigured`` comes from on the read path.** ``configured=False`` is
the ONLY route to it, and the engine SDK never passes it: the SDK always
resolves a store TARGET (a server URI, or the canonical embedded path via
``resolve_db_path()`` / ``FalkorProjection``'s no-arg fallback), so a store it
cannot OPEN is ``degraded`` (off by outage), not a set-up gap. The term stays
consumable here — and is asserted cross-lane — because the client boundary is
where a missing endpoint / key is a real condition, and a read that answered
must never be mislabelled regardless of what a caller wires to ``configured``.

**Cross-lane parity.** The client boundary (``client/tortoise_client/cli.py``,
``tortoise/tortoise_client.py``) reports the same four terms from that same
home module. ``tests/test_read_status.py`` asserts, term for term, that the
read path and ``tortoise.status_vocabulary.classify`` agree on the same
underlying condition, so the two surfaces cannot drift apart silently.

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

#: The recorded terms and the published term set, imported from their ONE
#: home. ``READ_STATUSES`` is the home's ``CLIENT_STATUS_TERMS``: the read path
#: reports the boundary's vocabulary, it does not own a copy of it.
from .status_vocabulary import (
    CLIENT_STATUS_TERMS,
    STATUS_AVAILABLE,
    STATUS_DEGRADED,
    STATUS_EMPTY,
    STATUS_UNCONFIGURED,
)
from .status_vocabulary import classify as classify_condition

# #4097: the truthy vocabulary has ONE declared home — importing it here rather
# than re-declaring the literal is what keeps the read-path flag consistent with
# every other boolean env read in the tree.
from .env_truthy import TRUTHY

READ_STATUSES: tuple[str, ...] = CLIENT_STATUS_TERMS

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
    return os.environ.get("TORTOISE_READ_STATUS", "").strip().lower() in TRUTHY


def classify_read_status(*, reached: bool, hit_count: int, degraded: bool,
                         configured: bool = True) -> str:
    """Map the read path's observed facts onto the recorded four terms.

    ``configured``  a store / endpoint was DECLARED — the read path reached a
                    projection object. ``False`` is the only route to
                    ``unconfigured``.
    ``reached``     the store ANSWERED.
    ``hit_count``   rows the read returned.
    ``degraded``    a leg did not run — the memory layer is impaired.

    The configuration / reachability / content mapping is DELEGATED to
    ``tortoise.status_vocabulary.classify`` (the recording home), so the two
    surfaces cannot diverge on it: configuration first (never declared →
    ``unconfigured``), then reachability (configured but no answer →
    ``degraded``), then content (``available`` / ``empty``). The read path's
    own ``degraded`` dimension folds in on top, and only AFTER that order — a
    store that was never declared stays ``unconfigured`` — but it outranks a
    successful read: when a leg did not run, a partial result is not a clean
    one and "nothing matched" is not established.
    """
    if reached or hit_count > 0:
        # A read that ANSWERED (or returned rows) proves a store was declared:
        # `unconfigured` — a set-up gap — must never describe it, whatever the
        # caller passed for ``configured``. This keeps the vocabulary's
        # invariant true at the classifier itself, not only at the SDK call
        # sites that happen to hard-code ``configured=True``.
        configured = True
    status = classify_condition(
        configured=configured, reached=reached, hits=hit_count)
    if status == STATUS_UNCONFIGURED:
        return status
    if degraded:
        return STATUS_DEGRADED
    return status


def classify_leg_trace(entries, *, hit_count: int,
                       reached: bool | None = None,
                       configured: bool = True) -> str:
    """Classify a read from its leg trace (R3 #1542 D4) and its hit count.

    ``configured`` is true by default because a leg trace only exists once the
    read path has a projection object; pass ``False`` when no store was
    declared at all.

    ``reached`` is derived BY DEFAULT (either rows came back, or at least one
    leg answered the store); it may be passed explicitly when the caller holds
    independent reachability proof (the read path's bounded probe).
    ``degraded`` is true when any leg recorded a degradation (a leg skipped, an
    index missing, a timeout) or when the TF-IDF fallback actually produced the
    rows. A zero-count fallback is a recovery that found nothing, not a
    degradation — the #2952 rule, so an honest no-match read on a healthy store
    is ``empty`` and not ``degraded``.

    A trace whose legs all failed (``query_failed`` / ``breaker_open``) derives
    ``reached=False``, and a CONFIGURED store that did not answer is
    ``degraded`` (off by outage), never ``unconfigured`` — that term is
    reserved for a store that was never declared.
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
        configured=configured,
        reached=(derived_reached if reached is None else reached),
        hit_count=hit_count,
        degraded=degraded,
    )


def combine_read_statuses(*statuses: str | None) -> str | None:
    """Coalesce the statuses of the calls that make up ONE composite read.

    A leg that returned hits or answered-and-found-nothing proves the store WAS
    reached — a store's configuration is one global fact, so a reached leg
    proves it was declared. A second leg that could not answer then makes the
    composite read INCOMPLETE (``degraded``), not absent: ``unconfigured``
    wins only when NO leg reached the store at all. This keeps the composite
    payload self-consistent — a read that returns rows can never simultaneously
    report ``unconfigured``.
    """
    present = [status for status in statuses if status]
    if not present:
        return None
    reached = any(
        status in (STATUS_AVAILABLE, STATUS_EMPTY) for status in present)
    impaired = any(
        status in (STATUS_DEGRADED, STATUS_UNCONFIGURED) for status in present)
    if reached and impaired:
        return STATUS_DEGRADED
    if reached:
        return STATUS_AVAILABLE if STATUS_AVAILABLE in present else STATUS_EMPTY
    # No leg reached the store. ``degraded`` asserts the store WAS configured,
    # so it outranks ``unconfigured`` (never declared) here.
    return STATUS_DEGRADED if STATUS_DEGRADED in present else STATUS_UNCONFIGURED
