"""The COMPLETENESS MARKER — §17.2, the input §17.3's router reads (#7952).

Postgres is the system of record and FalkorDB is a **derived hot cache**
(``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0, owner 2026-10-08). A
derived cache is **PARTIAL by design**, and a partial cache cannot tell two
events apart at its own read boundary:

    "the evidence I hold does not answer this"  → escalate a tier (§12.4)
    "this node is not in the cache at all"      → fetch from the record

Collapsed, the second becomes a **NEGATIVE FINDING**: the cache answers ``[]``
and the caller reads *"no such node"*. That is the one wrong answer that is
indistinguishable from a right one — both are ``200`` (§17.2).

This module is that marker's ONE home. It answers ONE question at the read
boundary: **could the store this read consulted SEE the answer?**

* ``hot``      — the store's declared coverage includes this read, so an empty
                 answer IS a finding of absence *within that store*.
* ``not-hot``  — the read falls outside the **known-hot set**. The store cannot
                 see it; the answer must be fetched from the record. It is a
                 **miss on residency**, never *"no such node"*.

⛔ **NOT A FIFTH STATUS TERM** (§17.3). The recorded read-status vocabulary is
``available | empty | degraded | unconfigured`` — four terms, ONE home,
``tortoise/status_vocabulary.py``, whose own text forbids a fifth. ``not-hot``
is a **directive about where to look**, not a status of the read: it says
*"ask the record"*, and the record's answer is what a status names. So it is
carried in its OWN field (``residency``, the ``response_fields`` pattern the
status field itself uses — #3863) and is never minted as a term.

The load-bearing consequence, stated against the code that could break it
(§17.3): under a PARTIAL declaration **``hits == 0`` alone no longer
establishes ``empty``**. A zero-hit read of a partially-covered store is a
residency miss, and when the truth fetch could not be made the read is
**``degraded``** — never ``empty``. ``apply_residency`` below is that rule, in
one place.

**The declaration (the known-hot set) is DATA — declared, never implicit**
(interface B / §2.2). This module reads it from the graph's own keyed-``:Meta``
idiom (the ``config_reset`` precedent), fail-safe: a missing, malformed or
unreadable declaration is ``complete`` — the §2.0 default while the FalkorDB
limit is held high, under which the marker CANNOT fire. ⚠️ That is *deferred,
not absent* (§17.3), and this ``:Meta`` node is the marker's interim home; the
control-plane residency metadata named on #7879 §2.6 and the cache projection
(#7971) own the authoritative declaration and replace the reader here — they
must not re-implement the classifier.

⛔ The marker is OFF BY DEFAULT. ``residency_enabled()`` reads
``TORTOISE_RESIDENCY`` (the #4097 truthy contract); unset or ``0`` leaves every
response byte-identical, and the read path never issues the declaration query
at all.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from .env_truthy import is_truthy

#: The two verdicts. ``not-hot`` is spelled exactly as §17.2/§17.3 record it.
RESIDENCY_HOT = "hot"
RESIDENCY_NOT_HOT = "not-hot"
RESIDENCY_VERDICTS: tuple[str, ...] = (RESIDENCY_HOT, RESIDENCY_NOT_HOT)

#: The declaration's two coverage states — the KNOWN-HOT SET's shape.
COVERAGE_COMPLETE = "complete"   # the store holds everything; the marker cannot fire
COVERAGE_PARTIAL = "partial"     # the store holds a declared subset
COVERAGE_STATES: tuple[str, ...] = (COVERAGE_COMPLETE, COVERAGE_PARTIAL)

#: The response field the marker rides. Its own field, never a status term.
RESIDENCY_FIELD = "residency"

#: The env flag that turns the field on (the #4097 truthy contract).
RESIDENCY_FLAG_ENV = "TORTOISE_RESIDENCY"

#: The keyed ``:Meta`` node the interim declaration lives on.
DECLARATION_META_KEY = "residency"


def residency_enabled() -> bool:
    """May the read surface EMIT the marker? (default off — #4097 truthy set)

    Off means the read path never reads a declaration and the response carries
    no ``residency`` key, so a default deployment is byte-identical.
    """
    return is_truthy(os.environ.get(RESIDENCY_FLAG_ENV))


@dataclass(frozen=True)
class ResidencyDeclaration:
    """The KNOWN-HOT SET, declared — interface B / §2.2.

    ``coverage``  ``complete`` (the default) or ``partial``.
    ``hot_keys``  for a ``partial`` declaration, the enumerated keys the store
                  claims to cover. ``None`` (an unenumerated partial store)
                  claims NO key: a node lookup that misses is a residency miss,
                  because nothing states the store ever held it.
    ``source``    provenance — where the declaration was read. ``None`` means
                  no declaration was found; a human-readable string names the
                  unreadable/malformed case so an operator can see WHY the
                  marker stayed quiet rather than assume coverage.
    """

    coverage: str = COVERAGE_COMPLETE
    hot_keys: frozenset[str] | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if self.coverage not in COVERAGE_STATES:
            raise ValueError(
                f"coverage must be one of {COVERAGE_STATES}, got "
                f"{self.coverage!r}")

    @property
    def partial(self) -> bool:
        return self.coverage == COVERAGE_PARTIAL

    def covers(self, key: str | None) -> bool:
        """Does the declared hot set include ``key``?

        A COMPLETE declaration covers everything (the cache is the whole
        graph). A PARTIAL declaration covers exactly the keys it ENUMERATES —
        an unenumerated partial store covers nothing, because *"we hold a
        subset"* is not a claim about any particular node.
        """
        if not self.partial:
            return True
        if key is None:
            return False
        return bool(self.hot_keys) and key in self.hot_keys


def mark_residency(declaration: ResidencyDeclaration, *,
                   hits: int, key: str | None = None) -> str:
    """The completeness marker for ONE read — ``hot`` or ``not-hot``.

    ``hits``  rows the read returned. ``> 0`` proves the store could see
              something the caller asked for, whatever the declaration says.
    ``key``   the node the read named, when it named one — the only case in
              which a declaration can separate *"outside the hot set"* from
              *"inside it and genuinely absent"*.

    The polarity is deliberate and must not be softened: a PARTIAL declaration
    turns a zero-hit read into ``not-hot`` UNLESS the read named a key the
    declaration covers. An empty result is never allowed to stand in for the
    marker — that is the whole defect §17.2 records.
    """
    if not declaration.partial:
        return RESIDENCY_HOT
    if hits > 0:
        # Something the caller asked for came back, so the store could see.
        return RESIDENCY_HOT
    if key is not None and declaration.covers(key):
        # Inside the known-hot set: a miss here IS a finding of absence within
        # the store, so `empty` (a status) can stand on its own.
        return RESIDENCY_HOT
    return RESIDENCY_NOT_HOT


def apply_residency(status: str | None, marker: str | None, *,
                    record_consulted: bool = False) -> str | None:
    """Fold the marker into the recorded status — §17.3's consequence, once.

    The four recorded terms keep naming what they name. The ONE change is the
    load-bearing one: a **``not-hot`` read may not be reported as ``empty``**.
    ``empty`` means *"the store was reached and held nothing"*, which a partial
    cache cannot establish. When the truth store was consulted and held nothing
    the read IS ``empty``; when it could not be consulted the read is impaired
    — ``degraded``, the recorded term for an impaired memory.

    Nothing else moves: ``available`` / ``degraded`` / ``unconfigured`` pass
    through untouched (a marker never upgrades a read, and never downgrades a
    store that failed to answer).
    """
    from .status_vocabulary import (
        STATUS_DEGRADED,
        STATUS_EMPTY,
    )

    if marker != RESIDENCY_NOT_HOT:
        return status
    if status != STATUS_EMPTY:
        return status
    if record_consulted:
        return status
    return STATUS_DEGRADED


def combine_residency(*markers: str | None) -> str | None:
    """Coalesce the markers of the legs that make up ONE composite read.

    A composite is a residency miss when ANY leg fell outside the known-hot
    set: the answer the caller receives is then incomplete, and the directive
    is *"ask the record"* either way. ``None`` when no leg was assessed — the
    caller asked for no marker, so the field stays absent rather than being
    filled with a verdict nothing measured.
    """
    present = [m for m in markers if m is not None]
    if not present:
        return None
    if RESIDENCY_NOT_HOT in present:
        return RESIDENCY_NOT_HOT
    return RESIDENCY_HOT


# ── the declaration's reader (the interim `:Meta` home) ─────────────────────


def _parse_hot_keys(raw: object) -> frozenset[str] | None:
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return None
    keys = frozenset(k for k in raw if isinstance(k, str))
    return keys or None


def read_declaration(g, *, timeout_ms: int = 500) -> ResidencyDeclaration:
    """Read the known-hot set from the graph's keyed ``:Meta`` marker.

    Fail-safe by design: the marker's failure direction is to stay QUIET, never
    to fire falsely. A missing declaration (the normal state — §2.0 keeps the
    FalkorDB limit high so the cache is complete), a malformed one (an
    unparseable ``coverage``), or a query that raises all return ``complete``,
    with ``source`` naming what happened so the state is visible rather than
    assumed. A bound is applied so a wedged store cannot stall the read the
    marker annotates.
    """
    try:
        rows = g.query(
            "MATCH (n:Meta {key:$key}) RETURN n.coverage, n.hot_keys",
            params={"key": DECLARATION_META_KEY},
            timeout=int(timeout_ms),
        ).result_set
    except Exception as e:
        return ResidencyDeclaration(source=f"unreadable:{type(e).__name__}")
    if not rows:
        return ResidencyDeclaration(source=None)
    row = rows[0]
    coverage = row[0] if len(row) > 0 else None
    hot_keys = _parse_hot_keys(row[1]) if len(row) > 1 else None
    if coverage not in COVERAGE_STATES:
        return ResidencyDeclaration(
            source=f"malformed:{coverage!r}")
    return ResidencyDeclaration(
        coverage=coverage,
        hot_keys=hot_keys,
        source=f"meta:{DECLARATION_META_KEY}",
    )


def declare_residency(g, declaration: ResidencyDeclaration) -> None:
    """Write the known-hot set onto the graph's ``:Meta`` marker.

    The declaration is graph-local operator metadata (the ``config_reset``
    precedent — a keyed ``:Meta`` node), not an ontology write: it carries no
    ``Point``, no edge and no EP weight. ⚠️ Interim home: the cache projection
    (#7971) and the control-plane residency metadata (§2.6) replace it, and
    they should keep using this writer so the format has ONE declaration.
    """
    props: dict[str, object] = {"coverage": declaration.coverage}
    # ALWAYS write the enumeration. ``SET n += $props`` is an ADDITIVE merge, so
    # omitting ``hot_keys`` for an unenumerated declaration would leave the
    # PREVIOUS enumeration on the node: a stale known-hot set the reader would
    # then report as coverage this declaration no longer claims (§17.2 — the
    # "empty is not a finding" defect the marker exists to prevent). ``[]``
    # reads back as ``None`` ("claims NO key") via ``_parse_hot_keys``, so the
    # write is a full REPLACE, not a merge.
    props["hot_keys"] = sorted(declaration.hot_keys or [])
    g.query(
        "MERGE (n:Meta {key:$key}) SET n += $props",
        params={"key": DECLARATION_META_KEY, "props": props},
    )


def clear_residency(g) -> bool:
    """Remove the declaration — back to ``complete`` (the §2.0 default)."""
    rows = g.query(
        "MATCH (n:Meta {key:$key}) RETURN 1",
        params={"key": DECLARATION_META_KEY},
    ).result_set
    if not rows:
        return False
    g.query(
        "MATCH (n:Meta {key:$key}) DETACH DELETE n",
        params={"key": DECLARATION_META_KEY},
    )
    return True
