"""The record↔cache reconciler's PRODUCTION TRIGGER (#9189 / §17.2).

``tortoise/reconciliation.py`` is the COMPARISON: it takes two keyed views and
reports divergence as integer counts, refusing when a side cannot be read.
Measured at ``f96a18da1`` it had **no production caller at all** — it was
exercised by tests and by the #7950 evidence probes only. §17.2 requires
*continuous reconciliation*: **divergence must be OBSERVED, not merely
recoverable**, and a check with no caller observes nothing.

This module is the trigger. It supplies the two production READERS, and one
pure composition that answers §17.2's two open questions per call: *what is
divergence* and *what is a residency outcome*.

THE TRIGGER DECISION, AND ITS DETECTION LATENCY
===============================================
**An on-demand / periodically-scheduled sweep, invoked through the CLI**
(``python -m tortoise record-reconcile``), not a call inside the capture turn.

* §17.2 says *continuous*; capture latency says *not on the turn*. A Postgres
  round-trip inside the capture turn is the same latency cost #9188 weighs for
  the write side, spent on a check that can be made later without changing what
  the user sees.
* The capture boundary is **not** the available alternative yet: #9187 (the
  record-write seam) and #9188 (the production writer) are the boundary, and
  until they land there is no capture turn that writes a record to observe.
  When they land, the hook is :func:`reconcile_record_and_cache` — one call,
  already pure, so the trigger can move without the comparison moving.
* **Detection latency: the sweep interval, named by whoever schedules it.**
  Nothing here chooses that number; the CLI is the thing a scheduler drives
  (the ``tools/install-*-schedule.sh`` precedent this repo already uses for the
  reaper and the queue reconcile).
* **NOT observed under this trigger:** (1) divergence created AND repaired
  between two sweeps; (2) divergence introduced by a direct cache write between
  sweeps, which is observed at the NEXT sweep rather than as it happens; (3)
  RELATIONS — the comparison is over node keys, because ``reconcile_views`` is
  (relation identity is a separate decision with its own contract, §Interface E);
  (4) **nodes under a label OUTSIDE the record placement** — the cache side is
  read over ``NODE_TABLES`` (the labels the projection writes), so a second
  writer's node under a label of its own (or under the cache's ``:Meta``
  bookkeeping label, which the residency declaration legitimately uses) is not
  an ``extra_in_cache``. ``extra_in_cache`` is the signature for keys the
  record PLACES, not a whole-graph census, and this trigger deliberately does
  not count or refuse unknown labels: refusing would make every cache
  unreadable (``:Meta`` is always there) and counting would report the
  declaration as a second writer; (5) **cache-HELD properties** — every
  property the cache holds that the partition did not judge, which is a
  DIFFERENT (larger) set than the one this bullet's earlier title named. Two
  facts make a name cache-held — it is outside its label's placement, AND the
  record side does not carry it — so FOUR populations reach it: (a) the EP and
  derived scalars §17.5 places on the cache (``ep_alpha``/``ep_beta``/
  ``ep_dirty``/``content_hash``/``confidence``/…), which the record schema
  REFUSES by name and so can never hold — the one §17.5 owns. ⚠️ ONE refused
  name the record's own derivation DOES supply: ``status`` (``Object``/
  ``Subject``) is refused as a record ``props`` KEY, but ``DERIVED_FROM_RECORD``
  and ``EVENT_STREAM_FOLDS`` synthesise it on the record side, so wherever the
  record DERIVES one the name is COMPARED and a mismatch reds; only a
  ``status`` the record derives NOWHERE reaches this bucket, and that is a
  lifecycle value, not "nothing to repair". ⛔ Say "derives", not "carries a
  carrier": ``DERIVED_FROM_RECORD`` reads the supersede columns, but
  ``EVENT_STREAM_FOLDS`` reads THREE more legs — the ``ObjectRegistered``/
  ``SubjectAdded`` birth record, an ``EntityMutated`` whose payload carries a
  STRING ``state.status``, and the connector/pack fold — so a node with no
  ``supersededBy``/``supersededAt`` can still be corroborated (measured: a
  carrier-less ``:Object`` with a birth record folds ``status='draft'`` and a
  disagreeing cache is a ``field_mismatches`` entry, not a bucket occurrence).
  ⛔ Its FIRST repair is to MINT that carrier, not to clear it: re-assert the
  value through the write door (``update_entity(…, status=<the value>)``),
  which journals the ``EntityMutated`` record whose string ``state.status`` the
  fold above reads — so the name becomes COMPARABLE with the cache's own value
  and the bucket empties while the value is PRESERVED. Measured: a record-side
  ``EntityMutated`` carrying ``state.status='archived'`` folds onto the row and
  ``graph_only`` goes ``(('status', 1),)`` -> ``()``; a mint carrying a
  DIFFERENT value reds as ``field_mismatches=1``, so a mint cannot launder a
  disagreement. ⚠️ The mint exists only for a STRING the label's status door
  ACCEPTS — any string on an ``:Object``, but on a ``:Subject`` only
  ``_SUBJECT_STATUS_VALUES``, so a ``:Subject`` value outside that set has NO
  mint and keeps only the clear below (`#11296`). A NON-STRING status (which the
  ``:Object`` door accepts, it validates nothing) has no USABLE mint either: the
  state-op leg keeps only a row whose ``payload -> 'state' -> 'status'`` is
  ``jsonb_typeof`` ``'string'``, so a minted ``5`` is DROPPED — the fold derives
  nothing, the name STAYS in ``graph_only``, and the pair still reads ``clean``.
  A non-string mint is a NO-OP for this comparison (``cache_projection``'s R4
  bound, `#9974`), not a red, so the value remains cache-only.
  ⛔ A CLEAR is the route only where the value is not trustworthy, and it is
  LABEL-DEPENDENT: on an ``:Object`` ``update_entity(…, status=None)`` is applied
  as ``SET n += {status: null}``, a REMOVAL, **only while the row is NOT
  terminal** — since #12882 a non-string status out of a DEAD state is REFUSED
  **when the call names the status ALONE** (it was the resurrection hole: the
  target is not a dead WORD, so a dead row was LIFTED instead of skipped), so the
  ``:Object`` clear is NOT a route for the TERMINAL case this repair exists for;
  on a ``:Subject`` the SAME call is
  REFUSED — ``sdk._update_entity``'s Subject status door validates against
  ``_SUBJECT_STATUS_VALUES``, and ``None`` is not one of them (the missing SDK
  door is `#11271`) — so a ``:Subject``'s clear is the graph-side ``REMOVE
  n.status`` or the wipe below, and it makes a TERMINAL entity read LIVE
  (``live._terminal_excluded`` reads an absent ``status`` as live).
  ⛔ WHAT IS PERFORMABLE ON A TERMINAL ``:Object`` TODAY is the COHERENT
  un-supersede only, and only for a ``superseded`` row: ``update_entity(…,
  status='live', supersededBy=None, supersededAt=None)`` — a CURRENT-READING
  word (``'live'`` on a plain kind; a pack kind's own current word goes in its
  place, as ``sdk.py``'s own refusal hint says) with BOTH carriers cleared in the
  SAME call, which #12825 restored and #12882 pinned. ⛔ **NOT the row's STORED
  word:** ``status='superseded'`` beside the two clears is ACCEPTED by the
  entailed short-circuit (``all(isinstance(s, str) and str.__eq__(s, _np_status)
  is True …)``, ``sdk.py`` — the ``s == _np_status`` form this sentence used to
  cite is GONE: it consulted the caller's ``__eq__``, #12896) and leaves the
  row TERMINAL *with its carriers gone* — the goal unmet and NO error reported.
  ⚠️ That row is not lost, but recovering it takes TWO accepted calls: re-plant a
  carrier (``update_entity(…, status='superseded', supersededBy='<successor>')``)
  and THEN the coherent clear above; measured, the final journal record carries
  ``status='live'`` with both carriers null, so graph and record agree. ⛔ Do NOT
  read "carrier-less" as "irreparable" and reach for a whole-graph restore — the
  wipe below is the last resort, not this one. Every OTHER terminal word has no
  SDK route **in one accepted call** (#12592), so naming the clear there would
  prescribe a call that raises (AX rule 2). ⚠️ Do NOT read that as "irreparable
  some other way": `archived`/`deprecated`/`retracted` DO lift in TWO accepted
  calls — reclassify to `superseded` with a successor, then the coherent clear
  above — which is the fail-open filed as #12908 (measured; the guard's answer
  then depends on how many calls a caller spends). It is NOT offered here as a
  repair: it fabricates the supersession it withdraws.
  ⛔ A COMPOSED NON-WORD IS NOT A REPAIR EITHER, AND IT IS NOW REFUSED: naming a
  non-word (``status=None``, ``123``, ``['live']``, ``True``) TOGETHER WITH both
  carrier clears USED to pass the coherent allowance — it keys on the carrier
  clear and, through ``_status_key_leaves_the_dead_state``, on the KEYS supplied,
  never on the value written — so the graph went CURRENT on a word nobody can
  read and, for the null shapes, this module's rebuild kept the ``superseded`` an
  earlier record folded (#9974's damage direction). Measured, and pre-existing;
  the allowance now requires a ``str`` target (review cycle 5), so those calls
  raise with the row unchanged. What is still unvalidated is an undeclared STRING
  word (``status='banana'``) — the ``:Object`` vocabulary hole, `#10371` /
  `#8166`. ⛔ Neither is
  a record-sourced re-projection, which is ADDITIVE (``MERGE … SET n += $props``,
  no REMOVE pass) and would leave the value in place — and a clear writes
  ``state.status = null`` into the record, which the state-op leg's
  ``jsonb_typeof(…) = 'string'`` filter DROPS (a JSON ``null`` is not
  ``'string'``); a ``:Subject``'s graph-side ``REMOVE`` journals no record row
  at all. Either way the record cannot re-derive the ABSENCE today
  (``cache_projection``'s R4 bound, `#9974`).
  (b) any property a SECOND WRITER put on a placed node, OR on a node the
  record does not own at all (``scan_cache_view`` fills ``placed`` for every
  cache node it reads, so such a node's out-of-placement names are bucketed
  too), which the record does not carry — §17.1's chokepoint signature,
  DECLARED BY NOTHING and reported rather than detected
  here (its repair is *find the writer*; measured at `#11184`'s review, filed
  `#11221`); and the two declared classes below, which the record's TABLE can
  hold and the record SIDE (this comparison's own projector-built map) omits —
  which is why they are bucketed here rather than compared. The distinction is
  load-bearing, not pedantry: a name is withheld from the comparison because
  ``record_props`` does not carry it, and for these classes that is because the
  projector declines them, not because the record has nothing to say. ⚠️ It is
  also PER ROW, not per class: the decline for ``_searchText`` is relaxed on a
  post-#10000 row (#10965), so the record side then HOLDS that one name, it is
  compared, and a cache that lost it is a ``field_mismatches`` entry — the
  residue `#11233` owns on the other instrument. ⛔ The row decides the
  ``story_arc`` CLASS too, and there it decides whether a clear is
  DESTRUCTIVE: the record holds it on a row whose ``public.{source,event}.story_arc``
  or ``public.session_narrative.narrative`` carries the prose, and holds NOTHING
  on every commit written before `#9555`'s ``SessionNarrated`` carrier — where the
  graph copy is the ONLY copy, so a per-class reading of this paragraph licenses
  the one clear that destroys it (see (5)).
  They are neither a mismatch nor invisible:
  :func:`reconcile_record_and_cache` partitions them into a named ``graph_only``
  count (the ``form_normalized`` precedent). ⚠️ THREE DECLARED classes arrive
  here — (b) is declared by nothing — and the partition BUCKETS every name it
  cannot judge rather than comparing it (⛔ not "every one reads clean": a
  property on a node the record does not own is bucketed while the NODE itself
  is an ``extra_in_cache`` divergence, so that pair reads DIVERGENT), while the
  three DECLARED ones differ in what repairs them: §17.5's cache-owned scalars,
  which the record schema REFUSES by name (a record read cannot produce them,
  nothing to repair — bar ``status``, which the record's own derivation makes
  COMPARABLE wherever the record DERIVES one, and a ``status`` the record
  derives NOWHERE is the form whose carrier can be MINTED — the value is
  re-asserted through the write door so the fold derives it, preserving it and
  emptying the bucket — with a DESTRUCTIVE CLEAR kept for an untrustworthy
  value, label-dependent and, on a ``:Subject`` outside its writable
  vocabulary, the ONLY route (`#11296`), see (a) above); the
  projection-DECLINED pair an EPISODIC Source's
  ``summary``/``_searchText`` make
  (``cache_projection.episodic_source_prose_properties()``) — which the record's
  TABLE holds and the record SIDE of this comparison omits, and which
  ``session_source_prose_migration.reconcile_session_source_prose`` (#11070)
  releases IN PLACE, ⛔ NOT a record-sourced re-projection: that write is
  ADDITIVE (``MERGE … SET n += $props``, no REMOVE pass), so a node that already
  carries the pair keeps it across an incremental projection
  (``cache_projection``'s own stated bound, #10852 review); and the
  NOT-PROJECTABLE columns (``cache_projection.CACHE_FORBIDDEN_COLUMNS``, #10394)
  — which the record's TABLE can hold and which no projection may ever write,
  because the graph is where they must not live. ⛔ Its repair is a DESTRUCTIVE
  CLEAR: the graph-side removal of the column itself (``REMOVE n.story_arc``) or
  ``record-restore`` / ``rebuild_from_record``, which wipe the graph first
  (``_wipe_all_nodes``, unconditional and whole-graph) and therefore clear a
  legacy copy too — ⚠️ and that wipe **cannot** be restricted to the rows where the
  record holds the text (``MATCH (n) DETACH DELETE n``, no label/``WHERE``/
  ``LIMIT``): a graph holding ANY row whose PROSE neither
  ``public.{source,event}.story_arc`` nor ``public.session_narrative.narrative``
  carries must NOT be restored at all — copy that prose out first, because on the
  pre-``#9555`` population (raw-Cypher write, never journalled;
  ``session_narrative`` TEXT columns measured NULL) the graph copy is the ONLY
  copy and the graph-wide wipe DESTROYS it (see (5)'s unit-is-the-GRAPH rule below, `#11325`) — and on a
  SERVER graph that restore is refused PRE-WIPE unless
  the graph name starts ``test_``/``tortoise_test`` (``record_restore._ask_the_wipe_guard``
  → the projection's ``_assert_test_graph``, #2944), and no production name does
  (``org_<org_id>``, its ``org_<org_id>_<gid>`` child and a legacy
  ``team_<org_id>`` alike; an embedded ``path=`` DB is exempt from that name
  check, not from supervision), so on a production cache no shipped route clears
  the PROPERTY on either ANCHOR while keeping the entity (``tortoise_delete``
  reaches the node only by REMOVING it, and journals the delete that removes its
  record row; ``update_entity(…, story_arc=None)`` is REFUSED as server-managed
  on BOTH anchors (``_SOURCE_SERVER_MANAGED_PROPS`` /
  ``_EVENT_SERVER_MANAGED_PROPS`` — each raises and the stored value is
  untouched, measured)) — ⚠️ **but the ``:Point`` half DOES have one, and it is
  non-destructive**: ``update_point(<id>, story_arc=None)`` clears the property
  and KEEPS the node (measured — the ``None`` passes
  ``sdk._refuse_unpaired_narrative``, whose refusal is on a NON-EMPTY narrative,
  and ``update_point``'s ``SET n += $props`` REMOVES a property set to ``None``;
  the journal records the ``None``, and the same live door is bound by the MCP
  tool ``tortoise_update_point``) — ⚠️ **and ONLY on the rows where the record still
  HOLDS the text** (``public.session_narrative.narrative`` for the session — ⛔ NOT
  the point's own ``props`` tail): where the narrative row does not carry it the
  point's copy is the only one NO SHIPPED route can put back on a production graph
  (the record's KEYED view does not hold it), and NO clear may be applied to it — on a production
  name the ``rebuild_all`` that would put it back is REFUSED PRE-WIPE, so the clear
  there deletes the prose rather than repairing the cache. ⛔ **The tail is excluded
  because the clear's OWN record write consumes it**: ``story_arc`` is no
  ``public.point`` column, so ``record_projection._split_properties`` sends the key
  to the tail (it has no null filter) and the record patch is a RIGHT-HAND merge
  (``props = props || %s::jsonb``) — so ``{"story_arc": null}`` OVERWRITES the prose
  that tail held, and a row whose only record home is the tail loses the last copy
  the record's KEYED VIEW holds to the very clear that counting it licensed (the
  append-only ``journal_event`` payload still carries the pre-clear prose — see the
  rebuild bound below, whose reach is what decides whether it can be put back). ⚠️ **Its durability is FOLD-DEPENDENT, and the
  bound is the JOURNAL one**: ``_revise_point``'s SET list is a closed set that
  names no ``story_arc`` clause, and the creation fold's open-set passthrough
  (``_persist_extra_props``, which ``_revise_point`` never calls) skips a ``None``
  by design — so a ``rebuild_all`` over a journal whose CREATION record carried
  the prose RESTORES it, and that is ``#10429``'s replay half. ⚠️ On a graph the
  rebuild can REACH: it asks the same L2 name guard as the restore
  (``_assert_test_graph``, refused PRE-WIPE on every production name), so a
  production server graph refuses the replay and the clear is durable there. The
  RECORD→CACHE lane does the opposite (the ``PointRevised`` patch stores
  ``props.story_arc = null``, ``_props_from_row`` returns it, and
  ``node_statement`` renders ``SET n += $props``, where a null REMOVES the key),
  so a record-sourced rebuild keeps it clear. ⚠️ So where a ``:Point``'s record
  ``props`` tail still CARRIES the prose, ``record-restore`` IS a repair that
  RESTORES the value — and ``_partition_cache_owned``'s
  ``keep |= set(record_props)`` makes that population COMPARED rather than
  bucketed, so it is not a ``graph_only`` occurrence at all. What has no repair
  that RESTORES the value is the two ANCHORS, not "no label". ⛔ The obstacle on those anchors is the
  PROHIBITION, not a hole in the schema: ``cache_projection.CACHE_FORBIDDEN_COLUMNS``
  forbids the RECORD→CACHE projection from writing the column there. ⚠️ **Whether a
  SOURCE actually holds the value is a PER-ROW fact, not a per-class one — key it
  on what the record HOLDS for this session (``public.{source,event}.story_arc``
  OR ``public.session_narrative.narrative``), never on how the prose arrived.**
  Where the record holds it, the graph copy is a stale duplicate and a clear frees
  it. ⚠️ Where NEITHER holds it — every commit written before ``#9555``'s
  ``SessionNarrated`` carrier, whose raw-Cypher ``s.story_arc=$arc`` /
  ``e.story_arc=$arc`` was NEVER journalled, and whose ``session_narrative`` TEXT
  columns measured NULL on every live capture (``hosted_api``'s 2b,
  ``#9567``/D7) — the graph copy is the ONLY copy, so NO clear may be applied
  THERE. ⛔ And the unit is the GRAPH, not the row: the destructive routes above
  wipe whole-graph (``MATCH (n) DETACH DELETE n``) and cannot be narrowed to the
  rows where the record holds the text, so a graph holding ANY row whose PROSE
  the record does not carry must NOT be restored at all — copy that prose out
  first. ⛔ Not
  "no IN-PLACE repair": a targeted ``REMOVE`` IS in place — and destructive, and
  issued by no instrument — and every targeted CLEAR on those two ANCHORS is
  REFUSED (``update_entity(…, story_arc=None)`` raises on BOTH); what is missing
  there is a route that KEEPS the value. ⚠️ No shipped
  instrument performs that removal for THIS class: ``#10017``'s purge
  (``tools/purge_legacy_turns.py``) is the precedent on a DIFFERENT population
  (legacy episodic turn ``:Point``s — a ``DETACH DELETE``) and names no
  ``story_arc`` at all, so citing it as the purger *for this class* sends an
  operator to a tool that leaves the divergence in place on either ANCHOR —
  it DOES clear a turn-shaped legacy ``:Point`` copy carrying the prose, by
  deleting the node. That is WHERE IT
  DIFFERS from the declined pair, which does have one, shipped:
  ``session_source_prose_migration.reconcile_session_source_prose`` (#11070, via
  ``#11152``) repairs or clears the pair IN PLACE from the record, no wipe, the
  node's other properties untouched. So the three classes differ in which lane
  clears them, not only in why the name must be absent. Counting them as
  ``field_mismatches`` would make the clean verdict UNREACHABLE on a
  journal-projected cache — every folded point carries them — which is exactly
  the "a signal indistinguishable from noise is not observability" failure
  this observer exists to prevent (#9196 review, P1).

WHERE THE NUMBER LANDS
======================
Three destinations, and a number nobody reads is not one of them:

1. **The structured log line** — :func:`log_result` emits
   :meth:`ReconciliationReport.render` through the ``tortoise.record_reconcile``
   logger, at a severity that follows the VERDICT: ``ERROR`` when the comparison
   could not be made (``unanswerable`` — a run that produced no verdict must not
   look like a quiet one), ``WARNING`` when anything is divergent, and ``INFO``
   only for ``clean``. A scheduled run's log is where an operator already looks,
   so the severity is part of the contract, not a formatting choice.
2. **The process EXIT CODE** — ``0`` clean · ``3`` divergence observed · ``2``
   the comparison could not be made. A scheduler alerts on non-zero, and the
   three verdicts are distinct so "cannot answer" never reads as "agreement"
   (§17.2, the #1382 class).
3. **stdout, with ``--json``** — the report's own machine-readable shape
   (``ReconciliationReport.as_dict``) so a dashboard or a cron wrapper can
   graph it instead of grepping a log.

⛔ **What this module is NOT.** It does not remediate (a divergence is observed,
not repaired — §17.2 is an observability requirement, and a repair that runs
before anyone sees the count is the failure mode it exists to prevent), it does
not re-decide residency (§17.3's rule is applied here by calling it, not by
restating it), and it adds no MCP tool and no public ``TortoiseSDK`` method.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .cache_projection import (
    KEY_PROPERTY,
    NODE_TABLES,
    RecordView,
    declared_key,
    episodic_source_prose_properties,
    is_episodic_source_node,
    projected_properties,
)
from .reconciliation import (
    SAMPLE_LIMIT,
    ReconciliationReport,
    ReconciliationUnavailable,
    reconcile_views,
)
from .record_projection import JournalCoverage
from .residency import ResidencyDeclaration

__all__ = [
    "CACHE_READ_TIMEOUT_MS",
    "EXIT_CLEAN",
    "EXIT_DIVERGENT",
    "EXIT_UNAVAILABLE",
    "LOG_NAME",
    "RECORD_CONNECT_TIMEOUT_S",
    "RECORD_STATEMENT_TIMEOUT_MS",
    "VERDICT_CLEAN",
    "VERDICT_DIVERGENT",
    "VERDICT_UNANSWERABLE",
    "VERDICT_UNPROJECTED",
    "CacheScan",
    "RecordReconcileResult",
    "exit_code_for",
    "log_result",
    "read_cache_view",
    "reconcile_record_and_cache",
    "record_key_map",
    "scan_cache_view",
]
#: The trigger's log channel. A separate name from the comparison module so a
#: deployment can route "the observer ran and found nothing" apart from
#: everything else ``tortoise`` logs.
LOG_NAME = "tortoise.record_reconcile"

#: The three verdicts. Deliberately three: ``UNANSWERABLE`` is what keeps an
#: empty or unreadable comparison from being reported as agreement.
VERDICT_CLEAN = "clean"
VERDICT_DIVERGENT = "divergent"
VERDICT_UNANSWERABLE = "unanswerable"
#: #13331 — a FOURTH answer, and it is not a cache divergence: the RECORD is
#: short of positions the JOURNAL holds. It is the one disagreement the
#: journal/Postgres split can produce (the chokepoint reports it per write as
#: `LiveWrite.unprojected`), the repair is `record_runner apply`, and folding it
#: into ``divergent`` would send an operator to look for a second cache writer
#: that does not exist.
VERDICT_UNPROJECTED = "unprojected_journal"

#: Process exit codes. ``2`` is the tree's "a surface could not be queried"
#: convention (``tools/collision_preflight.py``; the #3061 gate's INCOMPLETE),
#: and ``3`` is a real answer rather than an error. #13331's
#: ``unprojected_journal`` is a real answer too, so it shares ``3`` — a scheduler
#: that already alerts on divergence must alert on a lagging record as well.
EXIT_CLEAN = 0
EXIT_UNAVAILABLE = 2
EXIT_DIVERGENT = 3

#: The severity each verdict is logged at. Three verdicts, three levels: a
#: run that could not answer must not be quieter than a clean one.
_LOG_LEVEL = {
    VERDICT_CLEAN: logging.INFO,
    VERDICT_DIVERGENT: logging.WARNING,
    VERDICT_UNANSWERABLE: logging.ERROR,
    VERDICT_UNPROJECTED: logging.WARNING,
}

#: How long a single cache read may block — the DEFAULT, overridable per run
#: with the CLI's ``--timeout-ms``. The check annotates an operational sweep; a
#: wedged store must not stall it (the bound ``residency.read_declaration``
#: already applies to the sibling read). A read that hits this refuses (exit
#: ``2``) — it never returns a partial one.
CACHE_READ_TIMEOUT_MS = 2000

#: The RECORD side's two bounds, so a wedged Postgres cannot hang a scheduled
#: sweep either (the cache read above is bounded, and a check that blocks
#: forever on the other side is the same outage with half the fix).
#: ``CONNECT_TIMEOUT_S`` bounds the handshake; ``STATEMENT_TIMEOUT_MS`` is set
#: on the session (``-c statement_timeout=…``) so the server aborts a wedged
#: query rather than the client waiting on it.
RECORD_CONNECT_TIMEOUT_S = 5
RECORD_STATEMENT_TIMEOUT_MS = 5000


# ── the two readers ────────────────────────────────────────────────────────


def record_key_map(view: RecordView, *, scope: str = "") -> dict[str, dict]:
    """The RECORD side: ``{declared key: {property: value}}``.

    Keys are :func:`tortoise.cache_projection.declared_key` — ``label:key``,
    scope-prefixed when one graph holds several scopes — because the label is
    part of the coordinate (``Object`` and ``Subject`` both key on ``name``).
    The values are the node's REAL property maps: a reader that returned ``{}``
    per key makes ``field_mismatches`` STRUCTURALLY zero (``reconcile_views``
    compares two per-key mappings field by field, and two empty mappings always
    agree), so the count would prove key-set agreement only — the #8609 review's
    finding 3, guarded by :func:`reconcile_record_and_cache`.
    """
    return {
        declared_key(node.label, node.key, scope=scope): dict(node.props)
        for node in view.nodes
    }


@dataclass(frozen=True)
class CacheScan:
    """The cache side as the graph holds it, plus what the record cannot PLACE.

    ``view`` is the FULL property maps — the reader does not decide what is
    compared, because only the caller holding the RECORD side can tell a
    deliberately cache-owned property (§17.5: ``ep_alpha``/``content_hash``/…,
    which the record schema refuses) from a record-placed one the cache
    dropped. ``placed`` is, per key, the property names the record PLACES for
    its label (the placement table's projected set plus the contract key).

    It is a pair rather than a filtered ``view`` on purpose: the record's
    ``props`` tail is an OPEN set, so a name outside ``placed`` may still be
    record-held (a caller-supplied property the projection wrote verbatim). A
    blanket placement filter would drop it from the cache side and report a
    faithful projection as divergent — the same false red, mirrored. The
    comparison therefore excludes a property only when it is BOTH outside
    ``placed`` AND absent from the record (#9196 review, P1).
    """

    view: dict[str, dict]
    placed: dict[str, frozenset[str]]


def read_cache_view(g: Any, *, labels: Iterable[str] | None = None,
                    scope: str = "", timeout_ms: int = CACHE_READ_TIMEOUT_MS
                    ) -> dict[str, dict]:
    """The CACHE side as ``{declared key: {property: value}}`` — the FULL maps.

    This is :attr:`CacheScan.view`. A caller that also needs the record-placed
    split (the property names a cache-owned scalar must be told apart from) must
    use :func:`scan_cache_view` and pass ``placed=`` to
    :func:`reconcile_record_and_cache` — reading only this function and
    comparing it directly counts the projection's own graph-owned scalars as
    mismatches on a journal-projected cache (§17.5; #9196 review, P1).

    See :func:`scan_cache_view` for the read's bounds, refusals and duplicate
    rule.
    """
    return scan_cache_view(g, labels=labels, scope=scope,
                           timeout_ms=timeout_ms).view


def scan_cache_view(g: Any, *, labels: Iterable[str] | None = None,
                    scope: str = "", timeout_ms: int = CACHE_READ_TIMEOUT_MS
                    ) -> CacheScan:
    """Read the CACHE side ONCE, split into the view and its placement marks.

    ``labels`` defaults to every label the record places (``NODE_TABLES``). A
    label outside that set is refused rather than interpolated: the label is
    part of a Cypher statement, and the only labels this check may read are the
    ones the projection writes.

    ⚠️ **The read is never TRUNCATED — and it is BOUNDED.** A ``LIMIT`` would
    need truncation detection of its own to stay honest, and a silently
    truncated read UNDERCOUNTS ``extra_in_cache`` — the one number a second
    writer shows up in. What bounds the sweep is a wall-clock ``timeout_ms``
    (the CLI's ``--timeout-ms``, default :data:`CACHE_READ_TIMEOUT_MS`), which
    either returns the whole answer or REFUSES: it can never return a partial
    one, so bounding the cost cannot bias the count.

    ⚠️ **The cache side is the labels the RECORD places** (``NODE_TABLES``). A
    node under any other label is outside this comparison — see the module
    docstring's "NOT observed under this trigger" item (4); ``extra_in_cache``
    counts a second writer's nodes at coordinates the record OWNS, not the
    graph's whole population.

    ⛔ **Every failure is a REFUSAL.** A query that raises, a row that is not a
    property mapping, a node with no contract key, and **two nodes claiming one
    coordinate** all raise :class:`ReconciliationUnavailable`. Silence in any of
    those cases would read as *"the cache agrees with the record"* while the
    comparison had in fact not happened — and a node the reader skipped is
    exactly the ``extra_in_cache`` a second writer produces (the #1382 class
    §17.2 puts first).

    ⛔ **Duplicates are refused, not collapsed.** The writer ``MERGE``s on the
    key and the graph declares no uniqueness constraint, so a second writer's
    ``CREATE`` can leave TWO nodes at one ``(label, key)``. Collapsing them
    (last wins) leaves the two key SETS equal, so the run would report
    ``extra_in_cache == 0`` over a cache holding a node the record does not —
    the same undercount the key-less branch refuses, one step weaker. The
    comparison is therefore UNKNOWN, not clean.
    """
    known = frozenset(NODE_TABLES)
    wanted = tuple(NODE_TABLES) if labels is None else tuple(labels)
    unknown = sorted(set(wanted) - known)
    if unknown:
        raise ValueError(
            f"labels {unknown} are not record node tables — this check may only "
            f"read the labels the projection writes ({sorted(known)})")

    out: dict[str, dict] = {}
    placed: dict[str, frozenset[str]] = {}
    # §9.1/D1's narrowed pair, in the CACHE's vocabulary — resolved ONCE per
    # read (it is table-derived, so it must not be a module constant: see
    # `cache_projection._episodic_source_kind_property` for the installed-wheel
    # reason). ⚠️ Two DISJOINT sets, not one nested in the other (measured: this
    # pair is {summary, _searchText} and `cache_forbidden_columns("source")` is
    # {story_arc}). `placeable` below omits MORE than this set does — a column in
    # `CACHE_FORBIDDEN_COLUMNS` (§9.1/D1's narrative, #10394) is never projected
    # either — so it reaches `graph_only` through `projected_properties` rather
    # than through `declined`.
    declined = episodic_source_prose_properties()
    for label in wanted:
        # The names the record PLACES for this label: the placement table's
        # projected set (the schema's authority, minus what it REFUSES by name)
        # plus the contract key, which the projection writes under its own name
        # as well as using it as the MERGE key. A name outside this set is
        # either cache-owned (§17.5's EP/derived scalars — the record schema
        # refuses them) or a caller-supplied `props`-tail property, which the
        # RECORD side decides between; the reader marks, the composition judges.
        placeable = projected_properties(NODE_TABLES[label]) | {KEY_PROPERTY[label]}
        try:
            rows = g.query(
                f"MATCH (n:{label}) RETURN properties(n)",
                timeout=int(timeout_ms),
            ).result_set
        except Exception as e:
            # The driver's own message can carry the connection string (the
            # cache DSN holds a password), so only the TYPE is reported — the
            # same secret-hygiene rule the verb applies to the record side.
            raise ReconciliationUnavailable(
                f"cache is unreadable at label {label!r} "
                f"({type(e).__name__}) — divergence is UNKNOWN, not zero"
            ) from e
        key_property = KEY_PROPERTY[label]
        for row in rows:
            props = row[0] if row else None
            if not isinstance(props, Mapping):
                raise ReconciliationUnavailable(
                    f"cache returned a non-mapping property set at label "
                    f"{label!r} ({type(props).__name__}) — divergence is "
                    f"UNKNOWN, not zero")
            props = dict(props)
            key = props.get(key_property)
            if key is None or key == "":
                raise ReconciliationUnavailable(
                    f"a cache {label!r} node carries no contract key "
                    f"{key_property!r} — it cannot be addressed, and skipping "
                    f"it would undercount `extra_in_cache` (a second writer)")
            declared = declared_key(label, str(key), scope=scope)
            if declared in out:
                raise ReconciliationUnavailable(
                    f"the cache holds more than one {label!r} node at "
                    f"{declared!r} — collapsing them would leave the key sets "
                    f"equal and report `extra_in_cache=0` over a node the "
                    f"record does not have; divergence is UNKNOWN, not zero")
            out[declared] = props
            # §9.1/D1 (#10853): the projection DECLINES this pair for an
            # episodic Source, so a cache node that still carries it holds what
            # the projection will not write — CACHE-HELD, not "cache-owned"
            # (`graph_only_total`'s retraction: the record DOES hold this pair, so
            # the repair is a record-sourced clear, #11070), counted as
            # `graph_only` instead of as a divergence against a
            # record that is correct. Without this the reconciler would report
            # every session the current capture writes as divergent forever,
            # which is the "signal indistinguishable from noise" the module's own
            # docstring grades P1.
            if label == "Source" and is_episodic_source_node(props):
                placed[declared] = placeable - declined
            else:
                placed[declared] = placeable
    return CacheScan(view=out, placed=placed)


# ── the one composition ────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecordReconcileResult:
    """One trigger run: the comparison, and the keys it declined to judge.

    ``not_hot`` is separate from every count in ``report`` on purpose. A
    ``partial`` declaration says the cache holds a DECLARED SUBSET, so a record
    key the declaration does not enumerate is a **residency miss** — §17.3's
    ``not-hot``, repaired by fetching from the record — and NOT a projection
    lag. Counting it as ``missing_in_cache`` would conflate the two events
    §17.3 exists to separate, and would make a correctly-bounded projection
    report permanent divergence.
    """

    report: ReconciliationReport
    declaration: ResidencyDeclaration
    not_hot: int = 0
    #: Properties the CACHE holds that this projection will not judge — THREE
    #: DECLARED omission classes PLUS one population no declaration names — as
    #: ``(name, node count)``. The declared three: §17.5's EP/derived scalars
    #: (the record schema refuses them by name), the pair the §9.1/D1 narrowing
    #: declines for an EPISODIC Source
    #: (``cache_projection.episodic_source_prose_properties()``, which the RECORD
    #: does hold), and the NOT-PROJECTABLE columns
    #: (``cache_projection.CACHE_FORBIDDEN_COLUMNS`` — authority the record keeps
    #: and the cache must not materialise, ``#10394``), PLUS one population no
    #: declaration names — a property a SECOND WRITER put on a placed node the
    #: record does not carry, or on a node the record does not own at
    #: all (``#11221``; the bucket is partitioned OUT of ``field_mismatches``, so
    #: the placed-node case reads `clean` while a node the record does not own is
    #: an ``extra_in_cache`` divergence and reads DIVERGENT; either way the
    #: repair is
    #: *find the writer*) — as
    #: ``(name, node count)``. Kept OUT of ``field_mismatches``
    #: (counting them
    #: made the clean verdict unreachable on a journal-projected cache) and kept
    #: as its OWN count (a comparison that declines to judge a field has to say
    #: so — the ``form_normalized`` precedent). APPENDED LAST for the same
    #: positional-rebind reason ``form_normalized`` is.
    graph_only: tuple[tuple[str, int], ...] = ()

    #: #13331 — the RECORD's coverage of the JOURNAL, or ``None`` when the caller
    #: did not supply the journal's enumeration. This is the third leg of §17.2's
    #: observation: the cache comparison sees record↔cache, and a journal position
    #: the record never consumed is invisible to it (the cache row and the record
    #: row can agree perfectly while a whole write is missing from both). APPENDED
    #: LAST for the positional-rebind reason ``form_normalized`` is.
    journal: JournalCoverage | None = None

    @property
    def graph_only_total(self) -> int:
        """Properties the CACHE holds that the comparison did not judge.

        ⛔ NOT "cache-owned": `graph_only` carries THREE DECLARED omission
        classes plus one undeclared population (a second writer's property, which
        no declaration names — see the field's own declaration comment above,
        and :meth:`verdict` for how a clean verdict is reached), and `owned` is
        §17.5's term for only ONE of them — naming the total `owned` sends the
        operator to the wrong repair
        for the others, which is why the label :func:`log_result` renders says
        `cache-held`. This docstring must not keep the term the label retracted.
        ⚠️ Cited by SYMBOL, not by line: a line number in this module rots the
        same way a line in a Markdown table does, and the citation that used to
        be here already pointed one function away from the label it described.
        """
        return sum(count for _, count in self.graph_only)

    @property
    def unprojected_journal(self) -> int:
        """Journal positions the record does NOT hold (``0`` when not measured).

        ``0`` for a caller that supplied no journal enumeration is deliberate and
        is NOT a claim of coverage: :meth:`as_dict` reports ``None`` for BOTH the
        journal reading and this count in that case (``journal: null``,
        ``unprojected_journal: null``), so "we did not look" and "we looked and
        found none" stay distinguishable at the boundary — the `#5090` Q2 rule.
        ⛔ The in-memory ``0`` is what makes the four-verdict arithmetic total
        (:meth:`verdict`); it must never reach the WIRE as a measured number,
        which is why :meth:`as_dict` — not this property — is where the two
        states are separated (#13331 review F2).

        ⚠️ A leg that was SUPPLIED and could not be measured
        (``journal.unprojected is None``) also answers ``0`` here, for the same
        totality reason — but it is not clean: :meth:`verdict` returns
        ``unanswerable`` for it, so the zero never becomes a pass.
        """
        if self.journal is None:
            return 0
        return int(self.journal.unprojected or 0)

    @property
    def checked(self) -> int:
        return self.report.checked

    @property
    def divergent(self) -> int:
        return self.report.divergent

    def verdict(self) -> str:
        """``clean`` | ``divergent`` | ``unprojected_journal`` | ``unanswerable``.

        ``unanswerable`` when NOTHING was compared (``checked == 0``): an empty
        record and an empty cache agree perfectly, which is the vacuous
        agreement this whole surface exists to refuse. A run that compared
        nothing is not a clean bill of health.

        #13331 — a lagging record outranks a clean cache comparison, and LOSES to
        a cache divergence: the cache divergence is the more specific finding
        (a second writer, a failed fold), and an operator chasing a missing
        journal position must not be told the cache is fine when it is not.

        ⛔ A JOURNAL LEG THAT WAS ASKED FOR AND COULD NOT BE MEASURED is
        ``unanswerable``, never ``clean`` (`#5090` Q2). `JournalCoverage`
        reports ``unprojected=None`` when the caller supplied no enumeration
        (an unreadable journal, a path that is not there — #13331 review
        F3/B), and a ``clean`` verdict over a leg nobody could measure is the
        vacuous green this module refuses everywhere else: the operator is told
        the record holds every journal position when the count that would have
        shown a lag never ran.
        """
        if self.divergent > 0:
            return VERDICT_DIVERGENT
        if self.unprojected_journal > 0:
            return VERDICT_UNPROJECTED
        if self.journal is not None and self.journal.unprojected is None:
            return VERDICT_UNANSWERABLE
        if self.checked == 0:
            return VERDICT_UNANSWERABLE
        return VERDICT_CLEAN

    def as_dict(self) -> dict:
        payload = self.report.as_dict()
        payload.update({
            "verdict": self.verdict(),
            "not_hot": self.not_hot,
            "graph_only": dict(self.graph_only),
            "graph_only_total": self.graph_only_total,
            "declaration_coverage": self.declaration.coverage,
            "declaration_source": self.declaration.source,
            "journal": None if self.journal is None else self.journal.as_dict(),
            # ⛔ NOT `self.unprojected_journal` alone: that property answers ``0``
            # for a leg that never ran (so `verdict()` stays total), and a ``0``
            # on the wire is a MEASURED zero — `python -m tortoise
            # record-reconcile --json` without `--journal` would then tell a
            # scheduler the record holds the whole journal. The same holds for a
            # leg that RAN but could not count the journal (`journal.unprojected`
            # is ``None``, #13331 review F3/B): its count is unknown too, not 0.
            # "not measured" and "measured none" are reported differently, which
            # is the `#5090` Q2 rule this module applies everywhere else
            # (`#13331` review F2).
            "unprojected_journal": (None if self.journal is None
                                    or self.journal.unprojected is None
                                    else self.unprojected_journal),
        })
        return payload


def reconcile_record_and_cache(
        record: Mapping[str, Mapping],
        cache: Mapping[str, Mapping], *,
        declaration: ResidencyDeclaration | None = None,
        sample_limit: int = SAMPLE_LIMIT,
        placed: Mapping[str, frozenset[str]] | None = None,
        journal: JournalCoverage | None = None,
        ) -> RecordReconcileResult:
    """Compare the two keyed views, PARTITIONING divergence from residency.

    ``record``/``cache`` are ``{declared key: {property: value}}`` — already
    read, so this function is pure and cannot make a second connection to
    either store. ``declaration`` is the known-hot set (``residency``); ``None``
    means "not read", which is treated as COVERAGE-COMPLETE — the §2.0 default
    where the cache is the whole graph — so an absent key really is divergence.

    ``placed`` is :attr:`CacheScan.placed` — per key, the property names the
    record PLACES for its label. It enables the **cache-HELD partition** (⛔ not
    "cache-owned": the partition carries THREE DECLARED omission classes plus one
    undeclared population (a second writer's property, which no declaration
    names), only the first of
    which is §17.5-owned — ``_partition_cache_owned`` keeps its function name
    for history, but the term in prose is `cache-held`, matching the label
    :func:`log_result` renders): a
    property the cache holds that the record can neither place nor hold (the
    EP/derived scalars §17.5 assigns to the cache, which the record schema
    refuses by name) is NOT a field mismatch and is reported as its own named
    ``graph_only`` count instead. ⚠️ ``placed`` also omits the pair the §9.1/D1
    narrowing DECLINES for an EPISODIC Source — which the record DOES hold — so
    a ``graph_only`` name has one repair per population: none (schema-refused —
    bar ``status``: where the record DERIVES one the name is COMPARED, and its
    uncorroborated form is repaired by MINTING that carrier — re-assert the
    value through the write door (``update_entity(…, status=<the value>)``),
    which journals the ``EntityMutated`` record the derivation folds, so the
    name becomes comparable and the bucket empties with the value PRESERVED. The
    mint exists only for a STRING the label's status door ACCEPTS, so on a
    ``:Subject`` it is limited to ``SUBJECT_STATUS_VALUES`` and a value outside
    that set has no USABLE mint (`#11296`); a NON-STRING status has none either
    — the state-op leg keeps only a row whose ``payload -> 'state' ->
    'status'`` is a JSON string, so a minted ``5`` is DROPPED, the fold derives
    nothing, and the name STAYS in ``graph_only`` with the pair still reading
    ``clean``. A CLEAR is only for an
    untrustworthy value
    and is LABEL-DEPENDENT: ``update_entity(…, status=None)``, ``SET n +=
    {status: null}``, a REMOVAL, on an **non-terminal** ``:Object`` (since
    #12882 a non-string status out of a DEAD state is REFUSED when the call
    names the status ALONE, so it is not a route for the terminal case); on a
    ``:Subject`` that call
    is REFUSED (#1423's status door accepts only ``SUBJECT_STATUS_VALUES``, and
    ``None`` is not one of them), so a Subject's clear is the graph-side
    ``REMOVE n.status`` — and a clear makes a TERMINAL entity read LIVE. For a
    TERMINAL ``:Object`` the only performable route is the COHERENT un-supersede
    of a ``superseded`` row (``status='live'`` — a CURRENT-READING word, the pack
    kind's own word in place of ``'live'`` — with BOTH carriers cleared in the
    same call, #12825). ⛔ NOT the stored word: ``status='superseded'`` beside the
    two clears is ACCEPTED by the entailed short-circuit and leaves the row
    terminal with its carriers gone (measured). Every other terminal word has no
    SDK route in ONE accepted call (#12592); a two-call reclassification does
    lift them, and that is #12908's fail-open, not a repair. Clear,
    or the wipe, but ⛔ NOT a record-sourced re-projection, which is ADDITIVE and
    leaves the value in place — see the module docstring's (5)),
    a record-sourced clear IN PLACE for the declined pair —
    ``session_source_prose_migration.reconcile_session_source_prose`` (#11070),
    ⛔ NOT a re-projection, which is additive and leaves an existing pair on the
    node, the graph-side ``REMOVE`` for a not-projectable column (below), or *find
    the writer* for the undeclared second-writer population the partition also
    counts.

    ⛔ AND A THIRD OMISSION, from `#10394`: `cache_projection.
    CACHE_FORBIDDEN_COLUMNS` subtracts the NOT-PROJECTABLE class from
    `projected_properties`, so ``story_arc`` is not placeable either. That is a
    statement about the CACHE, not about the record — the column is PRIMARY
    there and §9.1/D1 forbids it only in the graph — so a legacy cache node
    that still carries the prose reads as a ``graph_only`` occurrence (one
    property the projection declines to write), NOT as a ``field_mismatches``
    against a record that is correct. The repair for that occurrence on either
    ANCHOR is a DESTRUCTIVE CLEAR, and its only RECORD-SOURCED route is a full
    ``record-restore`` — whose wipe is whole-graph and **cannot** be narrowed to
    those rows, so a graph holding ANY row whose prose neither
    ``public.{source,event}.story_arc`` nor ``public.session_narrative.narrative``
    carries must not be restored at all: copy that prose out first, because the
    wipe destroys it and the record has nothing to restore it from (the module
    docstring's (5) states the rule) — measured by
    `test_the_record_restore_writes_no_story_arc_on_any_node` arm (3), ⚠️ and
    refused PRE-WIPE on a production server graph name, #2944's L2 guard; or a
    graph-side ``REMOVE n.story_arc`` that no in-tree instrument issues today;
    ⚠️ a ``:Point`` copy is repairable in place ON THE ROWS WHERE THE RECORD STILL
    HOLDS THE TEXT (the module docstring's (5) states the per-row bound and its
    only-copy edge) —
    ``update_point(<id>, story_arc=None)`` clears the property and keeps the
    node (measured; pinned by
    `test_the_point_clear_removes_the_prose_and_keeps_the_node` — the clear, and
    the creation FOLD a journal replay routes through; ``rebuild_all``'s routing
    of that fold is unpinned).
    ⛔ NOT ``#10017``'s purge: ``tools/purge_legacy_turns.py`` deletes legacy
    episodic turn ``:Point``s — a different population, though a legacy turn
    ``:Point`` that carries the prose is cleared by it, by node deletion — and
    it never matches ``:Source``/``:Event``; what it contributes is durability
    for those ``:Point`` copies (its journaled turn removals stop a replay
    resurrecting them), not the clearing of this class. Never a destructive one
    for the ``:Point`` half — it has the non-destructive route named above, on the
    rows where the record still holds the text (where it does not, that clear
    leaves no copy a shipped route can put back on a production graph, and is not a
    repair at all).
    Nothing here performs either. ⚠️ **What the exclusion CAN and
    CANNOT hide, stated precisely**: `keep` includes ``record_props``, so a name
    the RECORD side carries is always compared — the exclusion cannot suppress a
    divergence in a caller-supplied ``props``-tail property. ⚠️ For `summary`,
    and for a LEGACY row's whole pair, it does suppress a VALUE comparison, and
    that is by construction, not an oversight: on this path the record side is
    ``read_record_view``, which builds every node through the same projector, so
    both sides omit those names and a disagreement inside them surfaces only as
    the ``graph_only`` occurrence count (a property present in the cache where
    the projection writes none). ⛔ NOT so for `_searchText` on a POST-#10000
    row: the projector PROJECTS it there (#10965), so the record side HOLDS it,
    `keep` includes it, and a cache that lost it is a ``field_mismatches``
    entry — measured at `#11224`'s review (1 mismatch, ``divergent``). That is
    the one name in the pair this instrument still compares, and it is why
    `record_serve.parity_difference`'s declined mirror forgives a real gap on
    exactly that population (filed `#11233`). An operator who wants the VALUES
    compared for the omitted names must read the record COLUMNS directly;
    the reconciler does not. Without it, a journal-projected cache — the
    shape production runs — reports a permanent ``field_mismatches`` on every
    point and can never be clean (#9196 review, P1).

    ⚠️ The exclusion is deliberately NARROW: a property absent from ``placed``
    is still compared when the record SIDE carries it (the record's ``props``
    tail is an open set), so a caller-supplied property the projection failed to
    write stays visible as divergence. ``placed=None`` (a caller with hand-built
    views) means "no placement knowledge" and compares every field exactly.

    ⛔ **A vacuous comparison REFUSES.** If either side is non-empty while every
    one of its property maps is empty, the field comparison would be
    structurally zero (the #8609 finding-3 defect) and the count would prove
    key-set agreement only. Raising is the fail-closed polarity: a comparison
    that cannot see values must not report agreement.
    """
    truth = dict(record)
    cached = dict(cache)

    _refuse_vacuous(truth, "record")
    _refuse_vacuous(cached, "cache")

    not_hot = 0
    if declaration is not None and declaration.partial:
        for key in [k for k in truth if k not in cached]:
            if not declaration.covers(key):
                # §17.3: the store never claimed this key. A residency miss,
                # not a projection lag — excluded from the comparison and
                # counted apart, never folded into `missing_in_cache`.
                del truth[key]
                not_hot += 1

    compared, graph_only = _partition_cache_owned(cached, truth, placed)
    report = reconcile_views(truth, compared, sample_limit=sample_limit)
    return RecordReconcileResult(
        report=report,
        declaration=declaration if declaration is not None
        else ResidencyDeclaration(source=None),
        not_hot=not_hot,
        graph_only=graph_only,
        journal=journal,
    )


def _partition_cache_owned(
        cache: Mapping[str, Mapping],
        truth: Mapping[str, Mapping],
        placed: Mapping[str, frozenset[str]] | None,
        ) -> tuple[dict[str, Any], tuple[tuple[str, int], ...]]:
    """Split the cache side into what is COMPARED and what is CACHE-HELD.

    ⛔ "cache-held" is the whole not-compared side, and it carries THREE DECLARED
    omission classes plus one undeclared population — a second writer's property
    (see the module docstring's (5) and :meth:`verdict`);
    "cache-owned" is §17.5's term for only ONE of them — the scalars the
    record CANNOT hold — so it is not the name of this partition.

    A cache property is excluded only when it is **both**

    * outside the record's placement for that label (``placed[key]`` — the
      schema-authorized property set plus the contract key), and
    * absent from the RECORD side for that key.

    The first half is what keeps §17.5's EP/derived scalars out of
    ``field_mismatches``: the record schema REFUSES ``ep_alpha``/``content_hash``/
    ``confidence``/… by name, so they can never be record-held, and counting
    their presence on ONE side as a mismatch makes every journal-projected cache
    permanently divergent (#9196 review, P1).

    The second half is what keeps the exclusion from swallowing real divergence:
    a record's ``props`` tail is an OPEN set, so a property outside the placement
    table may still be record-held — and then it is compared, exactly as before.

    ``placed=None`` means the caller has no placement knowledge (hand-built
    views): nothing is excluded, and every field is compared exactly.
    """
    if not placed:
        return dict(cache), ()

    compared: dict[str, Any] = {}
    counts: dict[str, int] = {}
    for key, props in cache.items():
        if not isinstance(props, Mapping):
            # A non-mapping value is compared WHOLE by `reconcile_views`; it
            # carries no property names to partition.
            compared[key] = props
            continue
        record_props = truth.get(key)
        keep = set(placed.get(key, frozenset()))
        if isinstance(record_props, Mapping):
            keep |= set(record_props)
        kept: dict[str, Any] = {}
        for name, value in props.items():
            if name in keep:
                kept[name] = value
            else:
                counts[name] = counts.get(name, 0) + 1
        compared[key] = kept
    return compared, tuple(sorted(counts.items()))


def _refuse_vacuous(view: Mapping[str, Mapping], side: str) -> None:
    if view and not any(view.values()):
        raise ReconciliationUnavailable(
            f"{side} returned EMPTY property maps for all {len(view)} keys — the "
            f"field comparison would be structurally zero, so the count would "
            f"prove key-set agreement only (the #8609 finding-3 defect); "
            f"divergence is UNKNOWN, not zero")


def log_result(result: RecordReconcileResult,
               logger: logging.Logger | None = None) -> str:
    """Land the number: one structured log line, severity following the VERDICT.

    ``ERROR`` for ``unanswerable`` (nothing was compared — a run that produced
    no verdict must not read as a quiet, healthy one), ``WARNING`` for
    ``divergent``, ``INFO`` only for ``clean``. The three verdicts get three
    levels because a scheduler's log is read at a glance, and the module's own
    thesis applies to severity too: a signal nobody can distinguish from
    silence is not observability.

    Returns the rendered line so a caller (the CLI) prints exactly what it
    logged — one spelling of the summary, not two.
    """
    line = result.report.render()
    if result.not_hot:
        line += f" [not_hot={result.not_hot} — residency, not divergence]"
    if result.graph_only_total:
        # A comparison that DECLINES to judge a field has to say so, or the
        # number it did report reads as covering everything it saw.
        names = ", ".join(name for name, _ in result.graph_only[:4])
        more = ", …" if len(result.graph_only) > 4 else ""
        # ⚠️ "cache-HELD", not "cache-OWNED" (#10394 review, P3). Each of the
        # THREE DECLARED omission classes is a property the CACHE holds — and
        # `graph_only` ALSO counts one population NO DECLARATION NAMES (a SECOND
        # WRITER's property on a placed node the record does not carry, or on a
        # node the record does not own at all; its repair is *find the writer*),
        # so the bucket is not the three-name set
        # this comment's earlier wording implied. "owned" is the §17.5
        # partition's term for ONE of the three (the EP/derived scalars the
        # record schema refuses, which only the cache has). The other two are
        # record-owned: the §9.1/D1 pair the narrowing DECLINES (the record
        # holds it, its repair is the IN-PLACE reconciler #11070 — ⛔ not a
        # re-projection, whose write is additive —) and the not-projectable
        # column (the record holds the text on the rows where it survives and the
        # graph must NOT — its repair there is a destructive clear: a full
        # restore's wipe — refused pre-wipe on a
        # production SERVER graph name, #2944 L2 — or a graph-side
        # REMOVE n.story_arc that no instrument ships today; ⚠️ on a `:Point` the
        # clear is `update_point(<id>, story_arc=None)`, which keeps the node and
        # needs no wipe — though its durability is the JOURNAL's only: a
        # `rebuild_all` over a journal whose creation record carried the prose
        # restores it (#10429), while the RECORD→CACHE rebuild replays the stored
        # null and keeps it clear. ⚠️ That is the bound's REACH, not an absolute:
        # `rebuild_all` asks the SAME L2 name guard as the restore
        # (`_assert_test_graph`, "REFUSING to run bulk DETACH DELETE on non-test
        # graph", applying to every SERVER graph name per #2944 — an embedded
        # `path=` DB does not face that name check), so on a production server graph the
        # replay is refused PRE-WIPE and the clear IS durable there); #10017's
        # purge is a DIFFERENT population — legacy episodic turn
        # :Points — and names no story_arc. Calling them all "owned" sends the
        # operator to the wrong repair.
        line += (f" [graph_only={result.graph_only_total} cache-held "
                 f"property occurrence(s) not compared ({names}{more})]")
    verdict = result.verdict()
    line = f"{line} verdict={verdict}"
    if result.journal is not None and result.journal.unprojected is None:
        # #13331 review F3/B — the leg RAN and the journal could not be
        # counted. Saying nothing here is the failure the whole leg exists to
        # prevent: the default line would be the unqualified `record == cache`
        # with `verdict=unanswerable` trailing it, and `record-reconcile
        # --journal <typo>` would read as a clean pass.
        line += (" [journal: the declared journal could NOT be counted, so the "
                 "leg is UNKNOWN — not clean; check the path exists and holds "
                 "the records a replay reads, then re-run]")
    elif result.journal is not None and result.unprojected_journal:
        # #13331 — the THIRD leg, rendered beside the cache comparison so a reader
        # cannot mistake a coherent cache for a coherent pair. The repair is named
        # because it is a single idempotent command, not an investigation.
        cov = result.journal
        line += (f" [journal: {cov.unprojected} of {cov.enumerated} enumerated "
                 f"position(s) NOT in the record (present={cov.present}, "
                 f"high={cov.high_seq}, first_gap={cov.first_gap}) — the record "
                 f"LAGS the journal; on a graph with no `record_cursor` row the "
                 f"repair is `tools/record_runner.py apply` (idempotent), and on "
                 f"a CUT-OVER graph that enumerated replay is refused by design "
                 f"(#9190) and no shipped verb heals it yet (#13338)]")
    (logger or logging.getLogger(LOG_NAME)).log(
        _LOG_LEVEL[verdict], "%s", line)
    return line


def exit_code_for(result: RecordReconcileResult) -> int:
    """``0`` clean · ``3`` divergence observed · ``2`` the comparison could not be made.

    #13331: ``unprojected_journal`` is a real ANSWER (the record lags the journal
    by N positions), so it exits ``3`` — the same code a scheduler already alerts
    on. Returning ``2`` there would report a measured disagreement as "no verdict",
    which is the reading that lets a lagging record sit unnoticed.
    """
    verdict = result.verdict()
    if verdict in (VERDICT_DIVERGENT, VERDICT_UNPROJECTED):
        return EXIT_DIVERGENT
    if verdict == VERDICT_CLEAN:
        return EXIT_CLEAN
    return EXIT_UNAVAILABLE
