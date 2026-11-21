"""The CACHE graph a record tenant is derived into — ONE declared mapping (#13337).

THE DEFECT THIS EXISTS FOR, MEASURED. A record tenant ``(org_id, graph_id)`` does
not name one cache graph, and the shipped consumers *derived* the name from the
CREDENTIAL that wrote instead of reading it from the tenant. For the same tenant
two credentials therefore disagreed:

===========================  ==================================  =======================
credential class             cache graph the write lands in       name this module returns
===========================  ==================================  =======================
graph-bound                  ``org_<org>_<gid>``                  ``graphs.namespace``
org-wide (default graph)     ``org_<org>``                        ``graphs.namespace``
===========================  ==================================  =======================

The record files BOTH writes under the same tenant, so the second row produced
``missing_in_cache = N`` on the canonically-*spelled* name while the write sat
safely in ``org_<org>`` — a **false divergence**, which ``record-reconcile``
reports as a real one and a scheduler alerts on. Measured on a throwaway tenant
with a real Postgres record and a real FalkorDB, one ``_data_sdk`` org-wide write
of an Object and an Event
(``docs/evidence/12943-cache-leg/orgwide-two-graphs.json``):

* record: 1 object + 1 event under ``(org, gid)``
* cache ``org_<org>`` (the FACT in ``graphs.namespace``): 1 Object + 1 Event
* cache ``org_<org>_<gid>`` (the canonical derivation): **ABSENT — the graph was
  never created**, not "present and empty" (the capture records
  ``GRAPH_NOT_FOUND``/``absent``, which is why the derived reconcile below grades
  every key missing rather than mismatched)
* ``record-reconcile --cache-graph org_<org>_<gid>``: ``divergent``,
  ``missing_in_cache=2`` — on a tenant whose cache is complete
* the same command on ``org_<org>``: ``missing_in_cache=0``, ``field_mismatches=6``
  (all six on the Event — that is #12949's root, projection fidelity, and this
  module does not touch it)

Nothing failed. The write path was coherent; the NAME was derived.

⛔ **WHAT THIS MODULE DOES NOT DO, so the fix cannot be read as a data move.**
It does NOT relocate, rename or re-open anything. ``org_<org>`` stays the default
graph's home — that is ``#10180``'s byte-identical guarantee, and reversing it is
an owner decision this module does not take. It reads the name that is ALREADY
true and hands it to the consumer, which is the acceptance
``#13337`` states: *"one declared function maps ``(org_id, graph_id)`` → the
cache graph, used by ``record-reconcile``, ``record-restore`` and the write path,
with the ``org_<org>`` legacy name carried as a fact read from
``graphs.namespace`` rather than derived."

⛔ **THE WRITE PATH NAMED IN THAT QUOTE IS NOT WIRED HERE, and the quote must not
be read as saying it is.** This module declares the mapping and the two VERBS
consume it (``git grep declared_cache_graph``: ``tortoise/__main__.py``, plus
this lane's evidence script — nothing on the write path). A write still names its
graph from the credential that wrote, which is why the org-wide row of the table
above is reachable in principle even after this change: the fix makes a reconcile
READ the name a tenant is already filed under, so it can no longer GRADE the
wrong coordinate — it does not move where a write LANDS. Wiring the write path is
a separate change (a per-tenant fact on the hot path, i.e. a control-plane lookup
per write), ``cache_leg.note_tenant_graph``'s documented silence for the
graph-id-less shape stays correct until one exists, and the write-path half of
``#13337``'s acceptance is DECLARED OPEN rather than quietly claimed.

WHY A READ AND NOT A DERIVATION. ``org_<org>_<graph_id>`` is the REGISTRY/ACL
spelling and is a correct guess for a graph created through ``_data_sdk`` — but
it is a guess, and the one graph it is wrong about is graph 0, the org's default,
whose namespace is the legacy ``org_<org>``. Guessing wrong there opens an EMPTY
graph, and every record key reports ``missing_in_cache``: a coherent cache grades
``divergent``. A wrong VERDICT is worse than no verdict, which is why the shipped
verbs refused ``--cache-graph``-less use rather than guess. They can stop
refusing *only* for the arm where a FACT is available — and both verbs still
refuse (exit 2) on the arm where nothing can answer, which is the recorded
decision ``#13337`` states and this module does not reverse.

THE SOURCES, in order, and each says which one answered:

1. :data:`SOURCE_FLAG` — the operator stated it (``--cache-graph``). A stated
   name is a fact; it is never overridden.
2. :data:`SOURCE_CONTROL_PLANE` — ``public.graphs.namespace`` through the control
   plane, the same read ``hosted_api._assert_graph_owned`` makes and the same
   surface ``record_writer.record_tenant_for`` derives the tenant from.
3. :data:`SOURCE_DERIVED` — :func:`canonical_cache_graph_name`, the registry
   spelling. ⛔ LABELLED, never presented as a fact, and NEVER GRADED: the two
   shipped consumers (``record-reconcile``, ``record-restore``) REFUSE on this
   source — exit 2, nothing compared / nothing wiped — because an assumed
   coordinate yields a wrong verdict (or an emptied graph) instead of no answer.
   It is returned so they can name the spelling they would have used. See
   ``#13337``'s recorded decision: the refusal "is correct and must stay".
   Only reachable when the control plane cannot answer (self-host, a
   registry-only deployment, an unreachable control plane).

⛔ **THE RECORD ROLE CANNOT ANSWER THIS.** ``public.graphs`` is not readable by
``tortoise_record_app`` (measured: ``permission denied for table graphs``), so the
fact cannot be read through the record DSN a ``record-reconcile`` run already
holds — which is exactly why the name was derived from the credential in the
first place. Nothing here widens that grant.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "SOURCE_CONTROL_PLANE",
    "SOURCE_DERIVED",
    "SOURCE_FLAG",
    "CacheGraph",
    "canonical_cache_graph_name",
    "declared_cache_graph",
    "namespace_from_control_plane",
]

#: The operator stated the name (``--cache-graph``, a restore's
#: ``--cache-graph``). A stated name is never overridden and never re-derived.
SOURCE_FLAG = "flag"

#: Read from the control plane — ``public.graphs.namespace``. A FACT.
SOURCE_CONTROL_PLANE = "control_plane"

#: The registry/ACL spelling, assumed. Reported as what it is.
SOURCE_DERIVED = "derived"


@dataclass(frozen=True)
class CacheGraph:
    """A cache graph name plus WHERE it came from.

    ``source`` is part of the answer rather than a log line because the three
    sources are not equally strong: a verdict on :data:`SOURCE_DERIVED` rests on
    an assumption, and a caller that cannot say so would present a guess with the
    same confidence as a read (`#5090`'s "not measured" vs "measured none",
    applied to a coordinate).
    """

    name: str
    source: str
    detail: str = ""

    @property
    def is_fact(self) -> bool:
        """Is this name ANSWERED rather than assumed?

        ⚠️ Read the name precisely (review P3). It means *"this coordinate is
        not the derived fallback"*, NOT *"this coordinate was READ":
        :data:`SOURCE_FLAG` is the operator's ASSERTION, which this code cannot
        verify and does not try to. So ``is_fact`` is a permission — "this name
        may be graded / wiped" — and the exact provenance is ``source``, which
        every consumer reports. A caller that needs "was it READ" must test
        ``source == SOURCE_CONTROL_PLANE``.
        """
        return self.source in (SOURCE_FLAG, SOURCE_CONTROL_PLANE)


def canonical_cache_graph_name(org_id: str, graph_id: str) -> str:
    """The registry/ACL spelling — ``org_<org_id>_<graph_id>``.

    The ONE derivation, so a caller cannot spell it a second way. It is the
    FALLBACK, not the answer: see this module's docstring for the one graph it is
    wrong about.
    """
    return f"org_{org_id}_{graph_id}"


def namespace_from_control_plane(org_id: str, graph_id: str) -> str | None:
    """``public.graphs.namespace`` for ``(org_id, graph_id)``; ``None`` if unknown.

    Mirrors ``hosted_api._assert_graph_owned``'s read — the same table, the same
    filters — because two readers of one fact must not disagree about it. It
    answers ``None`` on EVERY failure (control plane disabled, misconfigured, an
    absent row, a blank namespace, an unimportable client) rather than raising or
    guessing: this is a name resolution, and a name that could not be read must
    fall through to the labelled derivation, not turn a reconcile run into a 500.

    ⛔ THE VALUE IS WHITESPACE-TRIMMED, and that is a NORMALISATION rather than
    a rewrite: ``namespace`` is an operator-supplied TEXT column, so it can
    carry padding, and ``declared_cache_graph`` trims the STATED name (what an
    operator typed). Two readers of one value must agree on its normalisation or
    a caller that compares them (the verbs' "stated name disagrees with the fact"
    WARNING) prints a disagreement for an operator who stated exactly the fact —
    measured, with ``namespace = ' org_x '``. Trimming here means the ONE place a
    namespace enters the process is the ONE place it is normalised.
    """
    if not org_id or not graph_id:
        return None
    try:
        from .supabase_control import get_control_plane, is_supabase_enabled
    except Exception:  # noqa: BLE001, RUF100 — an optional dependency
        return None
    try:
        if not is_supabase_enabled():
            return None
        rows = get_control_plane().query(
            "graphs", select=["id", "namespace"],
            filters=[("id", "eq", graph_id), ("org_id", "eq", org_id)])
    except Exception:  # noqa: BLE001, RUF100 — see the docstring's "every failure"
        return None
    if not rows:
        return None
    namespace = rows[0].get("namespace")
    if not isinstance(namespace, str) or not namespace.strip():
        return None
    return namespace.strip()


def declared_cache_graph(*, org_id: str, graph_id: str,
                         cache_graph: str | None = None,
                         read_namespace=None) -> CacheGraph:
    """The cache graph for ONE record tenant — declared once, in source order.

    ``cache_graph`` is the operator's stated name (``--cache-graph``); it wins
    outright. Otherwise the control plane answers (``read_namespace`` overrides
    the read — the seam tests and a deployment with its own control plane use),
    and only when that cannot answer is the canonical spelling used, LABELLED
    :data:`SOURCE_DERIVED`.

    ``read_namespace`` is ``(org_id, graph_id) -> str | None``; it defaults to
    :func:`namespace_from_control_plane`. Passing it explicitly is how a caller
    that already holds the fact (a graph-bound credential carries its own
    ``graph_namespace``) states it without a second read.
    """
    stated = (cache_graph or "").strip()
    if stated:
        return CacheGraph(name=stated, source=SOURCE_FLAG,
                          detail="stated by the caller")
    read = read_namespace if read_namespace is not None else namespace_from_control_plane
    try:
        namespace = read(org_id, graph_id)
    except Exception:  # noqa: BLE001, RUF100 — a reader that raises answers nothing
        namespace = None
    if isinstance(namespace, str) and namespace.strip():
        return CacheGraph(name=namespace.strip(), source=SOURCE_CONTROL_PLANE,
                          detail=f"public.graphs.namespace for {graph_id}")
    return CacheGraph(name=canonical_cache_graph_name(org_id, graph_id),
                      source=SOURCE_DERIVED,
                      detail="no namespace fact — registry/ACL spelling assumed")
