"""The dense (vector) retrieval leg, served from the RECORD — #11642, #4051 V1.

THE DECISION THIS MODULE IS THE EXECUTING HALF OF, not one it makes:
`docs/architecture/SEARCH-ARCHITECTURE.md` (owner, 2026-11-07, PR #11481) item 1
— **the vector index and its payload live in Postgres** (`pgvector`). Items 2-4
are untouched here: the TEXT index stays in FalkorDB (a measured regression,
deferred behind M3), and traversal, writes and EP stay in the graph, so the
~1,900-statement Cypher surface is not in this module's scope.

WHAT THIS REPLACES, exactly. `search_engine.run_vector_query` reached FalkorDB's
`CALL db.idx.vector.queryNodes` in two engine signatures (A and B, #1359). Those
two calls were the whole executing surface of the move. This module is what they
become when a record tenant is armed; the graph branches remain, unchanged, as
the named fallback — because the payload is still IN the graph (writes stay
there, and that is why erasure has to span both stores).

THE THREE THINGS THIS MODULE REFUSES TO DO
==========================================

1. **It never invents a tenant.** The record's RLS policies read
   ``app.current_org_id`` (R6, #9554), so a read with no tenant is *zero rows*,
   not an error — i.e. a silent, total loss of the dense leg reported as
   "no matches". The scope comes from
   :func:`tortoise.record_writer.record_tenant`, the ONE declaration both
   credential lanes install (:func:`tortoise.record_writer.use_record_tenant`);
   this module resolves nothing itself and answers "not armed" rather than
   guessing. That is also why it is a *searcher factory*: the leg is only
   supplied when the scope is known.
2. **It never reports a partial answer as a complete one.** A filtered ANN with
   pgvector's default ``hnsw.iterative_scan = off`` returns FEWER rows than it
   owes — measured on 0.8.7 against a 20,000-row corpus: a filter matching 152
   rows returned **0** with the setting off and **150** with
   ``strict_order``, for a ``LIMIT 500``. The scan setting is therefore set
   explicitly per read, and the row count is compared against the material the
   filter selects, so a shortfall is visible (:class:`VectorShortfall`) rather
   than presented as the whole answer.
3. **It never changes the ranking silently.** The graph index it replaces was
   created with ``similarityFunction: 'cosine'``, and `run_vector_query`
   converts its ``1 - cosine`` distance back to a similarity; the Postgres
   indexes are ``vector_cosine_ops`` for that reason, this module returns the
   same **distance** shape the engine's YIELD produced, and the caller's ONE
   ordering pass (``#6214``/``#10121``) then runs over it unchanged. Measured
   top-10 parity against an exact scan on a 20,000 x 384 clustered corpus:
   25/25 probes identical, 250/250 members overlapping.

WHY A SEPARATE MODULE RATHER THAN A BRANCH IN `search_engine`
=============================================================
The connection, the tenant prologue and the fail-closed posture are
`record_serve`'s, and re-deriving them inside a search module is how two readers
of one record start disagreeing about which database they are reading.
:func:`tortoise.record_serve.open_record` is reused verbatim (bounded connect +
a transaction-local ``statement_timeout``), and the tenant prologue is
`record_projection.scope_connection` — the ONE function both record doors use.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "HNSW_ITERATIVE_SCAN",
    "LABEL_SERVED",
    "LOG_NAME",
    "RecordVectorError",
    "VectorShortfall",
    "armed",
    "searcher",
]

LOG_NAME = "tortoise.record_vector"

#: The pgvector session setting every read here installs, and WHY it is not left
#: at the default. `hnsw.iterative_scan` (pgvector 0.8.0+) decides what a
#: FILTERED index scan does when the filter is selective enough that the
#: `ef_search` window holds no match: ``off`` (the default) STOPS and returns
#: short. Measured on 0.8.7, 20,000 x 384, `ef_search` 40, a filter matching 152
#: rows and ``LIMIT 500``:
#:
#:   * ``off``          -> **0 rows** (the whole dense leg silently vanished)
#:   * ``strict_order`` -> 150 rows, still in distance order
#:   * ``relaxed_order``-> 150+ rows, possibly OUT of distance order
#:
#: ``strict_order`` rather than ``relaxed_order``: the caller already performs
#: one ordering pass over whatever it is handed, so ``relaxed_order`` would be
#: safe — but it is only safe *because of that pass*, and this module should not
#: depend on a detail two modules away for its correctness. Strict order costs a
#: re-sort inside pgvector and removes the question.
HNSW_ITERATIVE_SCAN = "strict_order"

#: ``entity_type -> (table, id column, kind filter, recency columns)``.
#:
#: ONLY the three labels whose vector the RECORD carries are here, and the
#: absence of ``object``/``subject`` is a decision, not an omission: their
#: vector is derived inside the graph fold from ``name`` (#7845) and rides no
#: journal record, so there is no column to read and nothing to move
#: (`20261109000001_record_embedding_vector.sql` states both rejected
#: candidates). They keep the brute-force graph scan they ALREADY used — the
#: graph has exactly one vector index, on `Point`, so those two were never
#: ann-served (`#4997`). :data:`LABEL_SERVED` is what tells the caller to take
#: the graph branch instead of pretending the record answered.
#:
#: The ``id column`` mirrors `search_engine`'s ``id_field`` derivation exactly
#: (#448): ``source``/``document`` answer the canonical key ``url``, ``event``
#: answers ``event_id``, everything else ``id``.
#:
#: The ``kind filter`` discriminates the two rows one table can hold — operators
#: are Points (``#172``), and a Document IS a Source (``D10``, §4.4) whose
#: counterpart is told apart by ``document_kind``.
#:
#: ⛔ THE OPERATOR FILTER IS THE CANONICAL IDENTITY, NOT ``is_operator IS TRUE``
#: (PR #11660 review F6). The repo's one definition of an operator is
#: ``is_operator = true OR op_type IS NOT NULL`` — the ``#943`` parity every
#: other operator read uses (``ep.py``, ``sdk.py``, ``search_engine.py``) — so a
#: HYBRID operator (``op_type`` set, the flag absent) is an operator everywhere
#: except in a leg that tests the flag alone. That miss is invisible rather than
#: loud: the graph branch keeps finding the node, so only a comparison between
#: the two legs sees the divergence, and the record leg is the one that now
#: carries production traffic. Pinned against a real Postgres in
#: ``tests/test_11642_vector_move_live.py::test_a_hybrid_operator_is_served_by_
#: the_canonical_identity``.
#:
#: ⚠️ THE THIRD DIVERGENCE, stated rather than fixed here: the GRAPH branch for
#: ``entity_type="operator"`` applies NO operator predicate at all (measured at
#: ``search_engine.py``'s two vector branches — only the ``scope_kinds`` probe
#: filters ``is_operator``), so on a box with no record armed an operator query
#: returns the nearest Points of ANY kind. That is a pre-existing defect on the
#: fallback path, not something this move introduced, and it is filed rather than
#: silently widened: until it lands, a hybrid operator is served by the RECORD
#: leg and missed by the graph leg, and a claim can be served by the graph leg
#: and never by the record leg. The record leg's answer is the correct one.
#:
#: The ``recency`` pair is ``(updated, created)``, applied by
#: :func:`_recency_sql` with the same CASE `search_engine.recency_key_expr`
#: uses. ``event`` has no ``created_at`` column, so its "created" half is
#: ``None`` and the expression degrades to ``coalesce(updated_at, '')`` — stated
#: here rather than left to be discovered as a divergence.
LABEL_SERVED: dict[str, tuple[str, str, str, tuple[str | None, str | None]]] = {
    "point": ("point", "id", "", ("updated_at", "created_at")),
    "operator": ("point", "id",
                 "(is_operator IS TRUE OR op_type IS NOT NULL)",
                 ("updated_at", "created_at")),
    "source": ("source", "url", "document_kind IS NULL",
               ("updated_at", "created_at")),
    "document": ("source", "url", "document_kind IS NOT NULL",
                 ("updated_at", "created_at")),
    "event": ("event", "event_id", "", ("updated_at", None)),
}


class RecordVectorError(RuntimeError):
    """The record could not answer the dense leg. NOT an absence."""


class VectorShortfall(RecordVectorError):
    """The record answered with FEWER rows than the filter selects.

    Raised into the caller rather than returned as a result: a short dense leg
    is exactly the ``#5090`` Q2 failure — *"nothing is silently invisible; a
    search finds a claim or says it could not look"* — and a caller that got a
    short list back has no way to tell it from a corpus that holds that many.
    """


@dataclass(frozen=True)
class _Scope:
    org_id: str
    graph_id: str


def armed(record_dsn: str | None = None) -> bool:
    """Is the record's dense leg available to this process, for this request?

    TWO questions, both answered by the modules that declare them: a record DSN
    resolves (:func:`tortoise.record_writer.record_dsn`, the arming surface) AND
    a record TENANT is installed on this request. Either answer ``False`` and
    the caller keeps the graph branch — which is correct, not degraded, because
    the graph still holds the payload.
    """
    return searcher(record_dsn) is not None


def _resolve_scope(carried: tuple[str, str] | None = None) -> _Scope | None:
    """The request's tenant, or ``None`` — never a guess.

    ⛔ THE RESOLUTION IS ``record_tenant_effective``, NOT ``record_tenant``, and
    the difference is a whole LANE (#11642 review round 2, P1). The tenants
    arrive by two different doors:

    * AMBIENT — the ``use_record_tenant`` ContextVar, which the MCP lane installs;
    * BY VALUE — ``sdk._record_tenant`` on the SDK the hosted REST lane opens
      (``hosted_api._data_sdk`` → ``_bind_record_tenant``, "#the auth seam is a
      plain dependency … holds no `with`").

    Reading the ContextVar alone answers "no tenant" on the hosted lane, so
    ``pg_vector`` would be ``None`` on every ``/v1/search`` and the two former
    ``db.idx.vector.queryNodes`` sites would NEVER read Postgres there — while
    this same change retires the in-graph index, turning every hosted dense read
    into a brute-force ``:Point`` scan. That is the silent-degradation class
    ``#10067`` already paid for once (its producer asked ``record_tenant`` while
    the write seam applied the carried tenant), which is why
    ``record_tenant_effective(carried=…)`` exists and why this is the ONE
    resolver that must be called here.

    A one-element or non-string answer is refused: a mis-scoped read returns ZERO
    rows under RLS with no error, so "half a tenant" is the one input that
    produces a silent empty leg.
    """
    from .record_writer import record_tenant_effective

    tenant = record_tenant_effective(carried=carried)
    if not isinstance(tenant, tuple) or len(tenant) != 2:
        return None
    org_id, graph_id = tenant
    if not isinstance(org_id, str) or not org_id:
        return None
    if not isinstance(graph_id, str) or not graph_id:
        return None
    return _Scope(org_id=org_id, graph_id=graph_id)


#: ``entity_type -> (status column, carries the legacy `outdated` flag)`` for the
#: labels whose dense leg must not leak TERMINAL rows (`#689`).
#:
#: ⛔ THIS IS NOT A SECOND VOCABULARY. The values come from
#: `search_engine._status_vocab_for`, the ONE family→vocabulary dispatcher, at
#: call time; this map only says WHICH COLUMN holds the status on each table and
#: whether the legacy `outdated` conjunct applies — facts about the SCHEMA, which
#: the record owns. `source`/`event` are absent because they carry no status on
#: the dense leg today: `search_engine._STATUS_FILTERED_LABELS` is
#: ``("Point", "Object", "Subject")``, and `Source`/`Event` are not in it — so
#: adding them here would filter rows the graph leg still returns.
_STATUS_COLUMN: dict[str, tuple[str, bool]] = {
    "point": ("status", True),
    "operator": ("status", True),
}


def _status_predicates(entity_type: str,
                      excluded_statuses: tuple | None) -> list[str]:
    """The terminal-status predicates the GRAPH branches apply, in SQL.

    ⛔ THE RECORD LEG MUST NOT BE A LOOPHOLE AROUND `#689`. Both graph branches of
    `run_vector_query` filter retracted/superseded Points out of the dense leg,
    and nothing downstream re-filters (`sdk.recall_state` applies its kind and
    scope filters, not the terminal-status one) — so a record leg that skipped
    this predicate would return exactly the rows `#689` exists to hide, on the
    branch that now carries production traffic. Measured by review, not by
    reading: the moved `#6214` pin passes `excluded_statuses=()` (the audit
    opt-in), so no pin would have caught it.

    ``excluded_statuses is ()`` is the caller's explicit "terminal rows IN" opt
    (the #1353 supersede-structure surface uses it), and the graph branches honor
    it by emitting NO clause — mirrored here, because a different reading would
    make the two legs disagree about the same request.
    """
    mapping = _STATUS_COLUMN.get(entity_type)
    if mapping is None or excluded_statuses == ():
        return []
    column, has_outdated_flag = mapping
    from .search_engine import _status_vocab_for

    vocabulary, include_outdated = _status_vocab_for(
        "Point" if entity_type == "operator" else entity_type.capitalize())
    literal = ", ".join(_sql_literal(value) for value in sorted(
        excluded_statuses or vocabulary))
    predicates = [f"coalesce(t.{column}, '') NOT IN ({literal})"]
    if has_outdated_flag and include_outdated:
        # The legacy flag conjunct, same polarity as the graph clause: a row is
        # excluded when it carries the flag TRUE.
        predicates.append("coalesce(t.outdated, false) IS NOT TRUE")
    return predicates


#: A status value is caller-independent data, never SQL — but `_status_vocab_for`
#: and the enum vocabularies are the only sources, and both are Python string
#: LITERALS. Quoting them here (rather than binding them) keeps the statement's
#: parameter count fixed, which the shortfall probe re-uses; the escape is
#: doubled so a value carrying a quote cannot break out.
def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _recency_sql(columns: tuple[str | None, str | None], alias: str = "t") -> str:
    """The recency projection, mirroring `search_engine.recency_key_expr`.

    The graph's expression is ``CASE WHEN coalesce(updatedAt, '') = '' THEN
    coalesce(createdAt, '') ELSE updatedAt END``, and the CASE (not a plain
    ``coalesce``) is load-bearing there: a ``coalesce`` stops at the first
    NON-NULL, so an ``updatedAt`` of ``''`` would suppress a valid ``createdAt``
    and sort the row as OLDEST in the window while the tie-break called it
    NEWEST. The same shape is emitted here, over the record's snake_case columns,
    so the two legs hand the caller's ONE sort the same key.
    """
    updated, created = columns
    if updated is None:
        return "''"
    if created is None:
        return f"coalesce({alias}.{updated}, '')"
    return (
        f"CASE WHEN coalesce({alias}.{updated}, '') = '' "
        f"THEN coalesce({alias}.{created}, '') ELSE {alias}.{updated} END"
    )


def searcher(
    record_dsn: str | None = None,
    carried: tuple[str, str] | None = None,
) -> Callable[[str, Sequence[float], int], list[tuple[str, float, Any]]] | None:
    """A dense searcher bound to this request's tenant, or ``None`` when unarmed.

    The returned callable has the signature
    ``search(entity_type, query_vec, limit) -> [(id, distance, recency), …]``
    and its shape is deliberately the SHAPE THE CYPHER YIELD PRODUCED, so
    `run_vector_query`'s single ordering pass consumes both legs identically and
    the ranking cannot drift between them by construction.

    ``carried`` is the request's ``(org_id, graph_id)`` when the caller holds it
    BY VALUE — the hosted REST lane's shape, where the tenant rides the SDK
    rather than a ContextVar. It is threaded to
    :func:`tortoise.record_writer.record_tenant_effective`, so both doors resolve
    identically; see :func:`_resolve_scope` for why reading the ContextVar alone
    would leave a whole lane unserved.

    ``None`` means "not armed" for every reason there is: no DSN, no tenant from
    either door, no driver. The caller takes the graph branch in all of them — it
    is the branch that has always answered, and the payload has not moved out of
    the graph.
    """
    try:
        from .record_writer import record_dsn as _resolve_record_dsn
    except Exception:  # noqa: BLE001, RUF100 — an unimportable arming module is "unarmed"
        return None
    try:
        dsn = _resolve_record_dsn(record_dsn)
    except Exception:  # noqa: BLE001, RUF100 — a malformed DSN is "unarmed" for a READ
        # ⛔ Deliberately NOT raised. This is the SEARCH path: the graph branch
        # is a correct answer, so a misconfigured DSN must not turn every
        # search into a 500. (`record_serve`, whose whole subject is proving an
        # ABSENCE, raises instead — the two postures differ because the
        # questions differ.)
        logging.getLogger(LOG_NAME).debug(
            "record DSN unusable — the dense leg stays on the graph")
        return None
    if not dsn:
        return None
    scope = _resolve_scope(carried)
    if scope is None:
        return None

    def _search(
        entity_type: str, query_vec: Sequence[float], limit: int,
        excluded_statuses: tuple | None = None,
    ) -> list[tuple[str, float, Any]] | None:
        return vector_search(scope, entity_type, query_vec, limit,
                             record_dsn=record_dsn,
                             excluded_statuses=excluded_statuses)

    return _search


def vector_search(
    scope: _Scope,
    entity_type: str,
    query_vec: Sequence[float],
    limit: int,
    *,
    record_dsn: str | None = None,
    excluded_statuses: tuple | None = None,
) -> list[tuple[str, float, Any]] | None:
    """The kNN read — ONE statement, tenant-scoped, shortfall asserted.

    THREE outcomes, and the third is the one that is easy to get wrong:

    * ``None`` — this ``entity_type`` is NOT served from the record at all
      (``object``/``subject``: no column, see :data:`LABEL_SERVED`). The caller
      MUST take its graph branch; this is not an empty answer and must never be
      rendered as one.
    * ``[]`` — served, asked, and the filter genuinely selects nothing.
    * rows — the answer.

    Raises :class:`RecordVectorError` when the record cannot answer (so the
    caller can take the graph branch and NAME the fallback), and
    :class:`VectorShortfall` when it answered with fewer rows than the filter
    selects (so the caller reports an incomplete answer rather than a small one).
    """
    mapping = LABEL_SERVED.get(entity_type)
    if mapping is None:
        # object / subject — see LABEL_SERVED. NOT an empty result: the caller's
        # graph branch is this label's real path and always was.
        return None
    table, id_column, kind_filter, recency_columns = mapping
    if limit <= 0:
        return []
    vec = [float(x) for x in query_vec]
    if not vec:
        return []

    from .record_projection import scope_connection
    from .record_serve import open_record

    where = ["t.embedding_vec IS NOT NULL"]
    if kind_filter:
        where.append(kind_filter)
    where.extend(_status_predicates(entity_type, excluded_statuses))
    predicate = " AND ".join(where)
    sql = (
        f"SELECT t.{id_column}, "
        f"(t.embedding_vec <=> %s::vector) AS distance, "
        f"{_recency_sql(recency_columns)} AS recency "
        f"FROM public.{table} AS t "
        f"WHERE t.org_id = %s AND t.graph_id = %s AND {predicate} "
        f"ORDER BY t.embedding_vec <=> %s::vector "
        f"LIMIT %s"
    )
    literal = "[" + ",".join(repr(v) for v in vec) + "]"
    params = (literal, scope.org_id, scope.graph_id, literal, int(limit))

    try:
        conn = open_record(record_dsn)
    except Exception as e:  # noqa: BLE001, RUF100 — every failure is "the record did not answer"
        raise RecordVectorError(
            f"the record could not be opened ({type(e).__name__}) — the dense "
            "leg did not answer") from e
    try:
        cur = scope_connection(conn, scope.org_id)
        try:
            # ⛔ TRANSACTION-LOCAL and set BEFORE the read: the default returns a
            # SHORT answer on a selective filter (0 rows where 152 exist —
            # measured, see HNSW_ITERATIVE_SCAN), and a short answer is
            # indistinguishable from a small corpus at the caller.
            cur.execute("SELECT set_config('hnsw.iterative_scan', %s, true)",
                        (HNSW_ITERATIVE_SCAN,))
            cur.execute(sql, params)
            rows = cur.fetchall()
            # ── §6.1's SHORTFALL ASSERTION, and its cost, stated ──────────
            # A short answer is only ONE of two things: a corpus smaller than
            # the limit (honest, and the common case for a small tenant) or a
            # scan that stopped (a lie). Nothing in the row shape can tell them
            # apart, so on the SHORT path only we ask the filter how much
            # material it selects: one bounded aggregate, paid once per short
            # dense read and never on a full one. It is not free and it is not
            # silent — a caller that wants it off must say so by arming nothing.
            if len(rows) < int(limit):
                cur.execute(
                    f"SELECT count(*) FROM public.{table} AS t "
                    f"WHERE t.org_id = %s AND t.graph_id = %s AND {predicate}",
                    (scope.org_id, scope.graph_id),
                )
                counted = cur.fetchone()
                available = int(counted[0]) if counted else 0
                if available > len(rows):
                    raise VectorShortfall(
                        f"the record's dense leg returned {len(rows)} of the "
                        f"{available} rows `{entity_type}` selects "
                        f"(limit {int(limit)}, hnsw.iterative_scan="
                        f"{HNSW_ITERATIVE_SCAN}) — a SHORT answer, not a small "
                        "corpus")
        finally:
            with contextlib.suppress(Exception):
                cur.close()
    except VectorShortfall:
        raise
    except Exception as e:  # noqa: BLE001, RUF100
        raise RecordVectorError(
            f"the record did not answer the dense leg ({type(e).__name__})"
        ) from e
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    out: list[tuple[str, float, Any]] = []
    for row in rows:
        key = row[0]
        if not isinstance(key, str) or not key:
            # A NULL key is not addressable by the caller (its `id_field` is the
            # join to the graph's node) — dropping it is the same rule the graph
            # leg's `WHERE n.<id_field> IS NOT NULL` applies.
            continue
        try:
            distance = float(row[1])
        except (TypeError, ValueError):
            continue
        out.append((key, distance, row[2] if len(row) > 2 else None))
    return out
