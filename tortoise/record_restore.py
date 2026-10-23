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
2. **A rebuild that materialised nothing is not a restore either.** Refused
   separately from (1), because it is a different failure AND a different
   state: the record HAD rows, the wipe ran, and the cache holds none of them.
   ⚠️ "Had rows" means either kind: a record that carries RELATIONS but no
   nodes (the measured shape when the record's relation endpoints are kept out,
   R4) whose relations cannot resolve their endpoints is the same destroyed
   cache, and it reached exit `0` under a `✓ restored` line until the guard was
   keyed on the record rather than on ``record_nodes``.
   ⚠️ That is reported as **UNKNOWN (`restore_unavailable`, exit 2), never as
   the exit-3 refusal class** — exit `3` means "REFUSED, nothing was written"
   and reads as "your cache is as it was", while here the cache has already
   been wiped by this run. ⛔ This guard is only worth having because the
   numbers it reads are MEASURED: ``rebuild_from_record`` reads its counts back
   off the graph after the write (``_project_record_view``), so a projection
   that wiped and materialised nothing really does report ``0``. When they were
   the plan's length, this refusal (and the report below it) could never fire
   and the printed ``N node(s) written`` was the plan's size — the #9556
   review's false-PASS finding.
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
Every count in the result is a MEASUREMENT, not a plan: ``nodes_written`` /
``relations_written`` are read back off the target graph AFTER the rebuild
(``_project_record_view``), so an operator's equality check against the record
is an assertion the code establishes rather than one it assumes.

The target graph's node count is read **before** the wipe and reported with the
result. It is informational — it never decides what to project (the record does,
§2.0's one-way rule) — and it is the number that makes a mistyped target
VISIBLE: ``target held 0 node(s) before the wipe`` beside ``wrote 412`` is a
correct restore into a minted-empty graph or a restore into the wrong one, and
only the operator can tell which. A restore that printed only "done" would hide
that distinction behind a green exit code.

A PARTIAL RESTORE IS NOT AN ABSENCE
-----------------------------------
``--limit`` bounds how many nodes are written, and it does not by itself decide the
declaration: a bound at or above the record's node count still covers every key the
plan could write and therefore declares ``complete`` (``bounded`` is not a state, it is
a flag). ``coverage`` follows the COUNTS, so a ``partial`` declaration also arises at
any bound when the record carried a row the cache cannot address (a blank-keyed
``:Session`` — see ``refused_keys``) — see §9's "bounded vs partial" bullet.
When the plan does fall short, the unwritten keys read as **``not-hot``** (§17.3):
a residency miss that ``degraded`` on read, never ``empty``. The outcome carries
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
from collections.abc import Mapping
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
#: The restore was refused BEFORE the wipe: an empty source or the cache's L2
#: guard refused the graph. **Nothing was written** — the distinction from
#: ``EXIT_UNAVAILABLE`` is what an operator's next step keys on, so ⛔ no
#: post-wipe outcome may be reported under it (`--limit < 1` is a usage refusal
#: inside this class' shape but exits ``2``, because it is a bad ARGUMENT rather
#: than a verdict about the cache).
EXIT_REFUSED = 3
#: No verdict was produced: no/invalid DSN, ``psycopg2`` absent, an unreadable
#: record or cache, a usage error — **or a failure once the rebuild started**, in
#: which case the cache may have been wiped and holds nothing (the caller must
#: treat it as UNKNOWN or LOST, never as untouched). Mirrors
#: ``record_reconcile.EXIT_UNAVAILABLE`` (and inherits its documented caveat:
#: argparse's own usage ``error()`` exits ``2`` too, so ``2`` means only *no
#: verdict was produced*).
EXIT_UNAVAILABLE = 2


class RestoreUnavailable(Exception):
    """No verdict — the restore could not be attempted, or it ran and its
    result cannot be established (the cache's state is UNKNOWN, or LOST)."""


class RestoreRefused(Exception):
    """The restore was refused BEFORE any write — nothing was written."""


def _as_int(value: Any, what: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise RestoreUnavailable(
            f"the rebuild reported {what}={value!r}, which is not a count — "
            f"the cache state is UNKNOWN") from e


def _as_optional_int(value: Any) -> int | None:
    """A count that may legitimately be ABSENT (a projection may not report it).

    Unlike :func:`_as_int`, "not reported" is not a fault here: the selected
    counts are diagnostics, so ``None`` stays ``None`` (and the outcome says so)
    rather than turning a landed restore into an UNKNOWN.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class RestoreOutcome:
    """What one record-sourced restore did, as counts an operator can check.

    ⛔ ``nodes_written`` / ``relations_written`` are MEASURED off the target
    graph after the rebuild, never the plan's length: they are the numbers an
    operator compares with the record to decide whether the restore landed, so
    a number the code did not read back would be a claim it does not establish.
    """

    org_id: str
    graph_id: str
    cache_graph: str
    record_nodes: int
    record_relations: int
    nodes_written: int
    relations_written: int
    #: ``None`` when the pre-wipe read could not be made (informational only).
    target_nodes_before: int | None
    #: What the projection SELECTED from the record (its plan size). Equal to
    #: :attr:`nodes_written` on a landed restore; kept so a run that selected
    #: more than it materialised is visible rather than silently reconciled.
    nodes_selected: int | None = None
    #: ⚠️ May legitimately exceed :attr:`relations_written`: a relation whose
    #: endpoint the projection does not resolve is a no-op (its statement
    #: MATCHes both endpoints), so selected != written is not by itself a fault.
    relations_selected: int | None = None
    #: The ``(label, key)`` rows the projection DECLINED to project (a row the
    #: cache cannot address — #9599's blank ``:Session`` key). These are why
    #: :attr:`nodes_selected` can be below ``record_nodes`` without anything
    #: being lost, and they are reported so the difference is auditable.
    refused_keys: tuple[tuple[str, str], ...] = ()
    #: ``complete`` / ``partial`` from the projection's own declaration.
    coverage: str | None = None
    #: Keys the declaration enumerated as known-hot. ⚠️ A ``complete``
    #: declaration enumerates NOTHING (``cache_projection.declaration_for``
    #: returns ``hot_keys=()`` when it covers the whole record), so this number
    #: is 0 on a perfectly complete restore — read :attr:`unwritten_keys`, never
    #: this, to decide whether anything was left out.
    declared_keys: int = 0
    #: ``--limit`` was supplied. ⚠️ NOT "something was left out": ``coverage``
    #: comes from the projection's own declaration, and a bound at or above the
    #: record's node count declares ``complete`` when every selected key landed
    #: — but that is a fact about the WRITES, not about the flag: a record row the
    #: projection REFUSES (a blank-keyed ``:Session``, :attr:`refused_keys`) makes
    #: the declaration ``partial`` at ANY bound, this one included. Read
    #: :attr:`unwritten_keys`, :attr:`refused_keys` and
    #: :attr:`relations_not_written` for what was actually left out — never this
    #: flag.
    bounded: bool = False
    extra: dict = field(default_factory=dict)

    @property
    def relations_not_written(self) -> int:
        """Record RELATIONS the rebuilt cache does not hold — ``relations-not-hot``.

        The relation half of the not-hot story, and the reason a ``complete``
        declaration is never, on its own, a claim about edges: ``coverage`` is
        derived from the record's NODE keys (``declaration_for``), so a restore
        that lands every node and no edge still declares ``complete``. That is
        not a hypothetical — a relation statement MATCHes its endpoints and is
        a no-op when the record does not carry them, which is the measured
        shape of an armed record (#9556 R4), so an undisclosed shortfall would
        let a green exit read as "every edge is back" (§17.3: a missing edge is
        a residency miss, never evidence that it does not exist).

        ``relations_selected`` is the right denominator when the projection
        reports it: a bounded run selects only the relations whose endpoints it
        keeps, so comparing against ``record_relations`` there would report a
        shortfall the bound itself explains.
        """
        expected = (self.relations_selected
                    if self.relations_selected is not None
                    else self.record_relations)
        return max(0, expected - self.relations_written)

    @property
    def declared_partial(self) -> tuple:
        """The DECLARED partial states this restore ended in (§17.3).

        Non-empty means the cache is not fully hot and the run says so in the
        machine-readable contract, so a consumer keyed on ``coverage`` alone
        cannot read it as complete: ``not-hot`` (nodes) and
        ``relations-not-hot`` (edges) are the two states, each with its own
        reason rather than being folded into an absence.
        """
        states = []
        if self.unwritten_keys:
            states.append("not-hot")
        if self.relations_not_written:
            states.append("relations-not-hot")
        return tuple(states)

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
            "nodes_selected": self.nodes_selected,
            "relations_selected": self.relations_selected,
            "refused_keys": [list(key) for key in self.refused_keys],
            "target_nodes_before": self.target_nodes_before,
            "coverage": self.coverage,
            "declared_keys": self.declared_keys,
            "unwritten_keys": self.unwritten_keys,
            "relations_not_written": self.relations_not_written,
            "declared_partial": list(self.declared_partial),
            "bounded": self.bounded,
            **self.extra,
        }

    def render(self) -> str:
        """ONE operator line — the MEASURED counts, the coverage, the not-hot set."""
        before = ("unknown" if self.target_nodes_before is None
                  else str(self.target_nodes_before))
        line = (
            f"restored {self.org_id}/{self.graph_id} -> cache "
            f"{self.cache_graph}: the cache now holds {self.nodes_written} "
            f"node(s) and {self.relations_written} relation(s) from the record "
            f"({self.record_nodes} record node(s), {self.record_relations} "
            f"record relation(s)); the target held {before} node(s) before the "
            f"wipe; coverage={self.coverage}")
        if (self.nodes_selected is not None
                and self.nodes_selected != self.nodes_written):
            # Should be impossible on a landed restore — say so rather than
            # letting two counts disagree in silence. `record_restore` refuses
            # this case outright; the line stays honest if a future caller
            # builds the outcome by hand.
            line += (f" — ⚠️ the projection SELECTED {self.nodes_selected} "
                     f"node(s), more than the cache holds")
        if self.refused_keys:
            # ⛔ The counter-signal to the not-hot remedy, and the reason it is
            # printed: a REFUSED row is unaddressable in this cache, so no
            # re-run (with or without `--limit`) can write it — without this
            # clause an operator reading only the human line is left to re-run a
            # destructive verb forever (§9's `not-hot` bullet).
            shown = ", ".join(f"{label}:{key!r}"
                              for label, key in self.refused_keys[:5])
            more = "" if len(self.refused_keys) <= 5 else \
                f" (+{len(self.refused_keys) - 5} more)"
            line += (
                f" — {len(self.refused_keys)} record row(s) were REFUSED by the "
                f"projection as unaddressable in this cache ({shown}{more}); "
                f"that is a record/cache addressability gap, NOT a shortfall, "
                f"and no re-run can write them")
        if self.unwritten_keys:
            line += (
                f" — {self.unwritten_keys} record key(s) were NOT written, so a "
                f"read for them is not-hot (degraded), never empty (§17.3)")
        if self.relations_not_written:
            # ⚠️ ``coverage`` above is a claim about NODES (``declaration_for``
            # keys off the record's node keys), so it stays ``complete`` here.
            # Saying so out loud is the whole point: an undisclosed relation
            # shortfall is the #3895 shape (an export whose edges are missing
            # while the counters look healthy).
            line += (
                f" — ⚠️ {self.relations_not_written} record relation(s) are NOT "
                f"in the cache (their endpoints are not resolvable there), so "
                f"`coverage` above covers NODES only; a read for one of those "
                f"edges is relations-not-hot, never empty (§17.3)")
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
    # ⛔ ``declared_partial`` matters as much as the coverage string: the
    # ``relations-not-hot`` state is DEFINED by ``coverage`` staying ``complete``
    # (every node landed, edges did not), so a selector that read coverage alone
    # logged exactly the state this field exists to surface at INFO — while a
    # ``--limit`` node-partial logged at WARNING. One rule for both partial
    # states, and the rule is the docstring's: a partial restore is a warning
    # because the cache answers not-hot for keys the operator may believe are
    # back.
    if outcome.declared_partial or (outcome.coverage
                                    and outcome.coverage != COVERAGE_COMPLETE):
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

    Refusals (:class:`RestoreRefused` — all of them PRE-wipe, so "nothing was
    written" is true of every one):
      * an empty view without ``allow_empty`` (see the module docstring, 1);
      * a wipe the cache's L2 guard refuses (a non-``test_`` server graph,
        asked BEFORE the rebuild — see :func:`_ask_the_wipe_guard`).
    Unavailability (:class:`RestoreUnavailable` — no verdict, the cache's state
    must be treated as UNKNOWN or as LOST): ``--limit`` below 1, a rebuild that
    materialised none of the record's rows, a rebuild whose measured counts
    disagree with the record, a non-mapping report, and **any failure raised
    during the rebuild** — the pre-wipe guard passed, so the wipe may already
    have run.

    ⚠️ ``--limit < 1`` is a BAD ARGUMENT (``RestoreUnavailable``, exit ``2``),
    not a verdict about the cache: nothing has been read or written at that
    point, and the CLI refuses it before the target graph is opened either.

    ⚠️ The two count checks read ``nodes_written``, which the projection now
    MEASURES off the graph after the write (#9556 review). They are therefore
    live guards on the shipped path rather than tautologies over the plan's
    length: a rebuild that wiped and materialised nothing reports ``0`` here and
    lands in the UNKNOWN class, not on exit ``0``.
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

    if not isinstance(result, Mapping):
        # A seam that returns something other than a report would otherwise
        # raise `AttributeError` PAST this module's exception boundary, i.e. a
        # traceback where the operator needs exit 2 and an UNKNOWN statement —
        # and the state really is unknown, because the rebuild has already run.
        raise RestoreUnavailable(
            f"the rebuild returned {type(result).__name__}, not a report — "
            f"the wipe may already have run, so the cache's state is UNKNOWN")

    nodes_written = _as_int(result.get("nodes_written"), "nodes_written")
    relations_written = _as_int(result.get("relations_written"),
                                "relations_written")
    # ⛔ Parsed BEFORE the zero-write guard (#9604 review). The guard below raises
    # for a wiped-and-empty cache, and it used to do so without ever reading
    # `refused_keys` — so a scope whose EVERY row was a row the projection
    # declines (a blank-keyed `:Session`, which the RECORD keeps and the cache
    # cannot address) reported LOST with no mention of the refusal that caused
    # it. The classification stays exit 2 (the cache really does hold none of
    # the record); only the datum that explains it is no longer discarded.
    nodes_selected = _as_optional_int(result.get("nodes_selected"))
    relations_selected = _as_optional_int(result.get("relations_selected"))
    refused_keys = tuple(
        (str(key[0]), str(key[1]))
        for key in (result.get("refused_keys") or ())
        if isinstance(key, (list, tuple)) and len(key) == 2)
    refused_note = ""
    if refused_keys:
        shown = ", ".join(f"{label}:{key!r}" for label, key in refused_keys[:5])
        more = "" if len(refused_keys) <= 5 else f" (+{len(refused_keys) - 5} more)"
        refused_note = (
            f" — {len(refused_keys)} of the record's row(s) were REFUSED by the "
            f"projection as unaddressable in this cache ({shown}{more}), which "
            f"is why nothing landed; that refusal is not itself a shortfall, "
            f"but the cache still holds none of the record")

    if (record_nodes or record_relations) and nodes_written == 0:
        # ⚠️ NOT the exit-3 class. Exit 3's documented meaning is "REFUSED,
        # nothing was written" — an operator reads that as "my cache is as it
        # was", and here it was WIPED by this run and holds none of the
        # record's rows. Post-wipe, so the state is reported as UNKNOWN (exit 2)
        # with the wipe spelled out.
        #
        # ⛔ The condition is about the RECORD having rows, not about it having
        # NODE rows (#9556 review). Keying it on ``record_nodes`` alone left a
        # relations-only scope (0 nodes, N relations — the measured shape when
        # the record's relation endpoints are kept out by the ``search_keys``
        # refusal, R4) unguarded on BOTH arms: this one was skipped by falsy
        # ``record_nodes``, and the ``expected_nodes`` check below was ``0 != 0``
        # — i.e. a run that wiped the cache and restored nothing exited 0 under
        # a ``✓ restored`` line. That is the exact false-PASS this verb exists
        # to refuse.
        raise RestoreUnavailable(
            f"the rebuild ran, wiped cache {cache_graph!r}, and the cache now "
            f"holds 0 node(s) while the record holds {record_nodes} node(s) and "
            f"{record_relations} relation(s) for {org_id}/{graph_id} — the wipe "
            f"happened and nothing was put back"
            f"{refused_note}. Treat the cache as LOST, not as restored and not "
            f"as it was; fix the cause and re-run `record-restore`")

    # ⛔ The NODE denominator prefers the projection's OWN plan size, for exactly
    # the reason `relations_not_written` does for edges. `record_nodes` is the
    # RAW record count, and since #9599 the projection REFUSES some record rows
    # on the way in: a blank-/whitespace-keyed `:Session` is kept by the RECORD
    # (it is the faithful carrier, and a Postgres key is addressable) but cannot
    # be a GRAPH key, so `_project_record_view` drops it from the plan. Comparing
    # the MEASURED count against the RAW one then reports a shortfall the
    # projection itself declared, on a restore that landed every writable row:
    # exit 2 "the cache state is UNKNOWN" over a correct cache, telling an
    # operator whose cache was just destroyed to "fix the cause and re-run" — a
    # remedy that can never succeed. `nodes_selected` is the post-refusal,
    # post-limit plan size, i.e. the only denominator that describes what THIS
    # run was supposed to write. A projection that reports none keeps the old
    # record-derived denominator, so the guard still discriminates.
    expected_nodes = (
        nodes_selected if nodes_selected is not None
        else (record_nodes if limit is None else min(record_nodes, limit))
    )
    if nodes_written != expected_nodes:
        # The MEASURED cache count is the authority here; the denominator is the
        # projection's plan size where it reports one, so a disagreement names
        # which side is wrong rather than blaming the record for rows the
        # projection was never going to write.
        raise RestoreUnavailable(
            f"the cache holds {nodes_written} node(s) from the record after "
            f"the rebuild, but the plan selected {expected_nodes}"
            + (f" of the record's {record_nodes}"
               if expected_nodes != record_nodes else "")
            + (f" (bounded by --limit {limit})" if limit is not None else "")
            + " — the restored cache and the source disagree, so the cache "
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
        nodes_selected=nodes_selected,
        relations_selected=relations_selected,
        refused_keys=refused_keys,
    )
