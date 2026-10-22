"""Restore a lost cache from the record — R8 (#9556).

``FalkorProjection.rebuild_from_record`` (``projection/__init__.py``) rebuilds
the cache from **the record** — Postgres's layer tables — instead of from the
journal. Measured for #9556 it had **no CLI verb and no production caller**
(``grep -rn 'rebuild_from_record(' tortoise/ | grep -v 'def '`` → 0 call
sites), so *"the graph is gone, restore it"* had a method and no operator
answer: the durability path existed and nothing could walk it.

This module is that answer's composition, and ``python -m tortoise
record-restore`` is its caller (a sibling of ``record-reconcile``: same DSN
resolution, same exit-code discipline, same "no verdict is not a pass" rule).
The runbook section that drives it is ``docs/infra-runbook.md`` §9.

WHAT A RESTORE MUST REFUSE — the four ways it could be worthless
================================================================
1. **An EMPTY source is not a restore.** A view with no rows wipes the cache
   and writes nothing, and the shipped path returns NORMALLY (`nodes_written:
   0`, `coverage: partial`) — an exit-code-0-shaped success that destroyed the
   cache. The commonest cause is a mistyped ``--org``/``--graph`` (a scope with
   no rows reads exactly like a scope that was never written), so the empty
   view refuses unless the operator says ``--allow-empty`` in so many words.
   This is the guard the verb exists behind, not a convenience: the destructive
   token (``confirm_destructive``) proves the *operator* authorized a wipe; it
   says nothing about whether there was anything to put back.
2. **A rebuild that wrote nothing is not a restore either.** Refused
   separately from (1), because it is a different failure: the record HAD rows
   and the projection reported ``nodes_written == 0``. A caller that trusts the
   return code without reading the counts cannot tell the two apart, so this
   raises rather than printing.
3. **A restore that cannot name its target refuses.** ``--cache-graph`` has no
   derived default (the record's ``graph_id`` scope column is NOT the cache
   graph's name): a wrong guess does not fail — FalkorDB opens an EMPTY graph —
   so the run would report a restore into a graph nobody reads while the
   operator's real graph stayed destroyed. Same rule, same reason, as
   ``record-reconcile``; for a restore the consequence is worse, which is why
   the pre-wipe node count is **reported** (below) as well as the name being
   required.
4. **The wipe the cache refuses is reported as a refusal, not as a crash.**
   ``rebuild_from_record`` runs the projection's destructive-op guard (L2,
   #2944): a server graph whose name does not start with ``test_``/
   ``tortoise_test`` is REFUSED. That guard fires on every production graph name
   (``org_<org_id>``), so the composition asks the projection's OWN guard
   before it builds a plan — the refusal is attributed exactly, with no
   message matching and no second copy of the name rule — and anything raised
   after that pre-flight is reported as UNKNOWN (``restore_unavailable``),
   never as "nothing was written", because by then the wipe may already have
   run.

WHAT IS REPORTED, AND WHY THE PRE-WIPE COUNT IS ONE OF THE NUMBERS
=================================================================
The target graph's node count is read **before** the wipe and reported with the
result. It is informational — it never decides what to project (the record does,
§2.0's one-way rule) — and it is the number that makes a mistyped target
VISIBLE: ``target held 0 node(s) before the wipe`` beside ``wrote 412`` is a
correct restore into a minted-empty graph or a restore into the wrong one, and
only the operator can tell which. A restore that printed only "done" would hide
that distinction behind a green exit code.

A PARTIAL RESTORE IS NOT AN ABSENCE
-----------------------------------
``--limit`` bounds how many nodes are written, so a bounded run declares
``coverage=partial`` and the unwritten keys read as **``not-hot``** (§17.3): a
residency miss that ``degraded`` on read, never ``empty``. The outcome carries
``unwritten_keys`` so an operator sees the count rather than inferring a
complete cache from a clean exit.

QUOTA (G5 on #9556)
-------------------
The restore door must never be refused by the customer's own storage allowance:
#9559 lands a fail-closed byte gate, and ``rebuild_from_record`` writes the
graph, so a gate on this path would let the BILLING path defeat the DURABILITY
path mid-rebuild. Nothing in this module consults an allowance, and when the
gate lands it must exempt this lane (or account it explicitly) rather than
inheriting the write-side check. That sentence lives here, next to the door it
constrains, because it is this issue's to state (#9556 integration note G5).

WHAT THIS DOES NOT DO (and must not be read as doing)
-----------------------------------------------------
* It does not migrate the owner's existing graphs into the record — deferred by
  owner statement (2026-10-22, #9567). A restore copies record → cache; a graph
  that only ever existed as a graph is not in the record to restore from.
* It does not serve reads, reconcile, or write the record. Read path: #7970;
  divergence: ``record-reconcile``; write path: ``record_writer``.
* It does not read the journal. Not one statement here touches a ``log_dir``,
  which is R8's indicator: *0 restore steps that require reading the journal
  instead of the record*. ``rebuild_all`` is deliberately NOT reachable from
  this module.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

#: The bound on the pre-wipe count read. Imported from the sibling verb that
#: bounds the SAME full-graph shape, so the two cannot drift apart.
from tortoise.cache_projection import NODE_TABLES
from tortoise.record_reconcile import CACHE_READ_TIMEOUT_MS
from tortoise.residency import COVERAGE_COMPLETE

__all__ = [
    "EXIT_REFUSED",
    "EXIT_RESTORED",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "RestoreOutcome",
    "RestoreRefused",
    "RestoreUnavailable",
    "log_result",
    "refuse_unrestorable_source",
    "restore_from_record_view",
]

LOG_NAME = "tortoise.record_restore"

#: The restore ran and the cache now holds the record's state.
EXIT_RESTORED = 0
#: The cache refused the wipe (L2), or the source/target could not be trusted
#: to make a restore meaningful. **Nothing was written** — the distinction from
#: ``EXIT_UNAVAILABLE`` is what an operator's next step keys on.
EXIT_REFUSED = 3
#: No verdict was produced: no/invalid DSN, ``psycopg2`` absent, an unreadable
#: record or cache, a usage error. Mirrors ``record_reconcile.EXIT_UNAVAILABLE``
#: (and inherits its documented caveat: argparse's own usage ``error()`` exits
#: ``2`` too, so ``2`` means only *no verdict was produced*).
EXIT_UNAVAILABLE = 2


class RestoreUnavailable(Exception):
    """The restore could not be ATTEMPTED — a verdict is not available."""


class RestoreRefused(Exception):
    """The restore was attempted and refused — nothing was written."""


def _as_int(value: Any, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise RestoreUnavailable(
            f"the rebuild reported {what}={value!r}, which is not a count — "
            f"the cache state is UNKNOWN") from e


@dataclass(frozen=True)
class RestoreOutcome:
    """What one record-sourced restore did, as counts an operator can check."""

    org_id: str
    graph_id: str
    cache_graph: str
    record_nodes: int
    record_relations: int
    nodes_written: int
    relations_written: int
    #: ``None`` when the pre-wipe read could not be made (informational only).
    target_nodes_before: int | None
    #: ``complete`` / ``partial`` from the projection's own declaration.
    coverage: str | None = None
    #: Keys the declaration enumerated as known-hot. ⚠️ A ``complete``
    #: declaration enumerates NOTHING (``cache_projection.declaration_for``
    #: returns ``hot_keys=()`` when it covers the whole record), so this number
    #: is 0 on a perfectly complete restore — read :attr:`unwritten_keys`, never
    #: this, to decide whether anything was left out.
    declared_keys: int = 0
    #: ``--limit`` was supplied, so ``coverage`` is ``partial`` by construction.
    bounded: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def unwritten_keys(self) -> int:
        """Record keys NOT written — the ``not-hot`` set of a partial restore.

        A ``complete`` declaration covers every record key by definition and
        enumerates none of them, so the only correct answer there is 0. This
        used to be ``record_nodes - declared_keys`` unconditionally, which
        reported EVERY complete restore as fully not-hot (measured: a 5-node
        full restore printed "5 record key(s) were NOT written").
        """
        if self.coverage == COVERAGE_COMPLETE:
            return 0
        return max(0, self.record_nodes - self.declared_keys)

    def as_dict(self) -> dict:
        return {
            "org_id": self.org_id,
            "graph_id": self.graph_id,
            "cache_graph": self.cache_graph,
            "record_nodes": self.record_nodes,
            "record_relations": self.record_relations,
            "nodes_written": self.nodes_written,
            "relations_written": self.relations_written,
            "target_nodes_before": self.target_nodes_before,
            "coverage": self.coverage,
            "declared_keys": self.declared_keys,
            "unwritten_keys": self.unwritten_keys,
            "bounded": self.bounded,
            **self.extra,
        }

    def render(self) -> str:
        """ONE operator line — the counts, the coverage, and the not-hot set."""
        before = ("unknown" if self.target_nodes_before is None
                  else str(self.target_nodes_before))
        line = (
            f"restored {self.org_id}/{self.graph_id} -> cache "
            f"{self.cache_graph}: {self.nodes_written} node(s), "
            f"{self.relations_written} relation(s) written from the record "
            f"({self.record_nodes} record node(s), {self.record_relations} "
            f"record relation(s)); the target held {before} node(s) before the "
            f"wipe; coverage={self.coverage}")
        if self.unwritten_keys:
            line += (
                f" — {self.unwritten_keys} record key(s) were NOT written, so a "
                f"read for them is not-hot (degraded), never empty (§17.3)")
        return line


def log_result(outcome: RestoreOutcome,
               logger: logging.Logger | None = None) -> str:
    """Log the result at a severity that follows the OUTCOME, and return it.

    A restore either restored or it did not; there is no "quiet" middle. A
    PARTIAL restore is a WARNING because the cache now answers not-hot for keys
    the operator may believe are back (the ``record_reconcile`` severity rule).
    """
    line = outcome.render()
    log = logger or logging.getLogger(LOG_NAME)
    if outcome.coverage and outcome.coverage != "complete":
        log.warning(line)
    else:
        log.info(line)
    return line


def _target_nodes_before(project: Any) -> int | None:
    """How many RESTORABLE nodes the target held BEFORE the wipe (or ``None``).

    Two things this count is not, both measured on the shipped entry point:

    * It is **not** ``MATCH (n) RETURN count(n)``. Opening a projection MERGEs
      its own bookkeeping marker (``:Meta {key:'point_fts_v2'}``,
      ``projection/__init__.py``), so a just-created, never-served graph reads
      ``1`` — and this number's whole job is to let an operator recognise a
      mistyped target. Counting only the labels the RECORD can actually put
      back (``cache_projection.NODE_TABLES`` — the projection's own node
      population) makes ``0`` mean what the operator needs it to mean. It is
      not a ``:Meta`` exclusion: ``:Meta`` also carries real state
      (``calibration_milestone`` is DATA), which is why the population is
      stated as "restorable" rather than "everything except bookkeeping".
    * It is **not** a decision. What gets projected is the record's, and the
      read is bounded by the sibling verb's ``CACHE_READ_TIMEOUT_MS`` — a
      full-graph count must not hang an operator's restore before it prints
      anything.

    An unreadable handle yields ``None`` and the render says ``unknown`` — a
    reporting gap must not be reported as a number.
    """
    g = getattr(project, "g", None)
    if g is None:
        return None
    try:
        rows = g.query(
            "MATCH (n) WHERE any(l IN labels(n) WHERE l IN $labels) "
            "RETURN count(n)",
            params={"labels": sorted(NODE_TABLES)},
            timeout=CACHE_READ_TIMEOUT_MS).result_set
    except Exception:
        return None
    if not rows or not rows[0]:
        return None
    try:
        return int(rows[0][0])
    except (TypeError, ValueError):
        return None


def _ask_the_wipe_guard(project: Any, cache_graph: str) -> None:
    """Ask the projection's OWN L2 guard whether this target may be wiped.

    ``rebuild_from_record`` runs ``_assert_test_graph`` (#2944) and refuses a
    server graph whose name is not ``test_``/``tortoise_test``-prefixed — which
    is EVERY production graph name (``org_<org_id>``). Asking the projection
    rather than re-implementing the rule keeps ONE name rule in the tree (the
    reason ``_assert_test_graph`` takes an explicit ``graph_name`` at all), and
    it gives the refusal a PRE-WIPE attribution: anything raised after this
    point may have run the wipe, so it is reported as UNKNOWN instead of as
    "nothing was written".

    A projection that does not expose the guard (a test double) is not asked —
    the real guard still runs inside the rebuild, and its refusal then lands in
    the post-preflight UNKNOWN class, which is the honest reading of a refusal
    this function could not place.
    """
    guard = getattr(project, "_assert_test_graph", None)
    if not callable(guard):
        return
    try:
        guard(f"REFUSING to run bulk DETACH DELETE on non-test graph "
              f"(record-restore into {cache_graph!r})")
    except RuntimeError as e:
        raise RestoreRefused(
            f"the cache refused the restore: {e} — the operator's next step is "
            f"a decision about this graph's disposability (#2944's "
            f"disposable-graph registry), not a retry") from e


def refuse_unrestorable_source(view: Any, *, org_id: str, graph_id: str,
                              cache_graph: str, allow_empty: bool = False,
                              limit: int | None = None) -> None:
    """The refusals that need NO cache: an empty source, and a bound below 1.

    Split out so the CLI can run them **before it opens the target graph**.
    Opening a projection is not free of side effects — its construction MERGEs
    a ``:Meta {key:'point_fts_v2'}`` bookkeeping marker and therefore CREATES a
    mistyped graph — so a run that is going to refuse must refuse first. A
    mistyped ``--org/--graph`` is the commonest cause of an empty source, which
    makes this the exact case where touching the named graph is worst.

    The composition calls this too (idempotent), so a caller that skips it is
    still refused — the CLI calls it early for the SIDE EFFECT, not to make the
    guard exist.
    """
    record_nodes = len(getattr(view, "nodes", ()) or ())
    record_relations = len(getattr(view, "relations", ()) or ())

    if record_nodes == 0 and record_relations == 0 and not allow_empty:
        raise RestoreRefused(
            f"the record holds NO rows for scope {org_id}/{graph_id}, so this "
            f"run would WIPE cache {cache_graph!r} and put nothing back. A "
            f"scope that was never written reads exactly like a mistyped "
            f"--org/--graph, so the empty source is refused: check the scope "
            f"against the record, or pass --allow-empty to state that wiping "
            f"this cache is what you meant")

    if limit is not None and limit <= 0:
        # ⚠️ 0 is not a harmless bound. It reaches the projection, which slices
        # `nodes[:0]` and STILL WIPES (the wipe precedes the projection), so a
        # scripted `--limit 0` would destroy the cache and — before this guard —
        # report it under exit 3, whose documented meaning is "nothing was
        # written". No operator's restore means "wipe and put nothing back";
        # that intent is `--allow-empty` over an empty record, not this.
        raise RestoreUnavailable(
            f"--limit must be >= 1, got {limit} — 0 wipes the cache and writes "
            f"nothing, so it is refused before any write. Nothing was "
            f"restored")


def restore_from_record_view(view: Any, *, org_id: str, graph_id: str,
                             cache_graph: str, project: Any,
                             allow_empty: bool = False,
                             limit: int | None = None,
                             scope: str = "") -> RestoreOutcome:
    """Restore ``cache_graph`` from ``view`` (the record's state for one scope).

    ``project`` is a ``FalkorProjection`` built over the TARGET graph — the
    caller owns building it and closing it. ``confirm_destructive=True`` is
    passed HERE, at this entry point, for the same reason ``_cmd_rebuild``
    passes it at its own: the human running the restore is the authorization,
    and a future caller of ``rebuild_from_record`` must not be able to wipe a
    graph by forgetting it.

    Refusals (:class:`RestoreRefused`):
      * an empty view without ``allow_empty`` (see the module docstring, 1);
      * a wipe the cache's L2 guard refuses (a non-``test_`` server graph,
        asked BEFORE the rebuild — see :func:`_ask_the_wipe_guard`);
      * a rebuild that reported writing no nodes while the record held some.
    Unavailability (:class:`RestoreUnavailable` — no verdict, the cache's state
    must be treated as UNKNOWN): ``--limit`` below 1, a rebuild whose reported
    counts contradict the record, and **any failure raised during the rebuild**
    — the pre-wipe guard passed, so the wipe may already have run.

    ⚠️ "Nothing was written" is about the RESTORE'S OWN STATE. The two
    cache-side refusals happen before the wipe and write none of the record's
    rows, but opening the projection can create the target graph and MERGE its
    ``:Meta`` bookkeeping marker — which is why the two source-side refusals are
    split out above, and why every message names what was and was not written
    instead of saying "nothing happened".
    """
    refuse_unrestorable_source(view, org_id=org_id, graph_id=graph_id,
                               cache_graph=cache_graph,
                               allow_empty=allow_empty, limit=limit)
    record_nodes = len(getattr(view, "nodes", ()) or ())
    record_relations = len(getattr(view, "relations", ()) or ())

    target_before = _target_nodes_before(project)

    # Attribute the wipe guard BEFORE building anything: the projection's L2
    # check (#2944) is the ONE name rule, so it is ASKED rather than
    # re-implemented, and a refusal it raises is provably pre-wipe. Everything
    # after this point may have run the wipe, so it is classified as UNKNOWN —
    # a `RuntimeError` subclass raised post-wipe (e.g. `DirectCacheWriteRefused`
    # from `project_records`) must never be reported as "REFUSED, nothing was
    # written".
    _ask_the_wipe_guard(project, cache_graph)

    try:
        result = project.rebuild_from_record(
            view, confirm_destructive=True, scope=scope, limit=limit)
    except (RestoreRefused, RestoreUnavailable):
        raise
    except Exception as e:
        raise RestoreUnavailable(
            f"the restore failed DURING the rebuild ({type(e).__name__}: {e}) "
            f"— the pre-wipe guard passed, so the wipe may already have run "
            f"and the cache's state is UNKNOWN. Re-run `record-restore` once "
            f"the cause is fixed; do not read the cache as empty") from e

    nodes_written = _as_int(result.get("nodes_written"), "nodes_written")
    relations_written = _as_int(result.get("relations_written"),
                                "relations_written")

    if record_nodes and nodes_written == 0:
        raise RestoreRefused(
            f"the rebuild wrote 0 node(s) while the record holds "
            f"{record_nodes} for {org_id}/{graph_id} — a rebuild that writes "
            f"nothing is not a restore, and the cache has already been wiped")

    expected_nodes = record_nodes if limit is None else min(record_nodes,
                                                            limit)
    if nodes_written != expected_nodes:
        raise RestoreUnavailable(
            f"the rebuild reported {nodes_written} node(s) written but the "
            f"record holds {record_nodes}"
            + (f" (bounded by --limit {limit} to {expected_nodes})"
               if limit is not None else "")
            + " — the reported restore and the source disagree, so the cache "
              "state is UNKNOWN")

    return RestoreOutcome(
        org_id=org_id,
        graph_id=graph_id,
        cache_graph=cache_graph,
        record_nodes=record_nodes,
        record_relations=record_relations,
        nodes_written=nodes_written,
        relations_written=relations_written,
        target_nodes_before=target_before,
        coverage=result.get("coverage"),
        declared_keys=_as_int(result.get("declared_keys"), "declared_keys"),
        bounded=limit is not None,
    )
