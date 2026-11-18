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
import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ARMING_ENV",
    "HNSW_ITERATIVE_SCAN",
    "LABEL_SERVED",
    "LOG_NAME",
    "READINESS_TTL_SECONDS",
    "RECORD_JOURNAL_ENV",
    "RecordVectorDeclined",
    "RecordVectorError",
    "VectorShortfall",
    "armed",
    "cutover_armed",
    "journal_ready",
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
#: ⛔ `object`/`subject` ARE SERVED, AND THE GATE THAT HELD THEM BACK IS NOW
#: PAID (#13422) — the record leg applies `#689`'s terminal-status cage through
#: the RECORD'S OWN FOLD, not through a status column. The history matters
#: because the shape of the old refusal is what the next reader will re-derive:
#:
#:   * `search_engine._STATUS_FILTERED_LABELS` is ``("Point", "Object", "Subject")``,
#:     so BOTH graph branches drop a superseded/deprecated/archived/retracted
#:     entity out of the dense leg, and :func:`_status_predicates` exists precisely
#:     so the record leg is not a loophole around that;
#:   * the RECORD has no `status` COLUMN for these two labels — it is a
#:     projection-owned CACHE (`OpDispatch.never_store`, §2.6) and
#:     `chk_object_props_no_derived` refuses it in the props tail;
#:   * the SQL view (`public.object_current_status`) answers only
#:     `live`/`superseded` — **and the leg does not read it.** `#13335` is still
#:     open, and it is a gap in that VIEW; it was never a gap in the record.
#:
#: ⇒ WHAT ACTUALLY OPENS THE GATE, and why it is not a two-dict-entry change.
#: The record's status fold already expresses all four terminal words, because
#: the fold is not the view: `cache_projection.apply_event_stream_folds` replays
#: `public.journal_event` — the record's own append-only truth — through the SAME
#: `status_folds` vocabulary and the same per-event guard the live graph fold
#: applies (`projection/entities.py::_fold_object_status`), including the
#: `EntityMutated` `state.status` carrier `#9974` restored. So the set of keys a
#: label's fold calls TERMINAL is computable from the record WITHOUT a status
#: column, without a grant on the procedural views (`#10193` — the layer tables
#: carry their own SELECT policy, which is what this leg reads), and without
#: restating the predicate here.
#:
#: ⚠️ THE TWO PATHS ARE NOT THE SAME QUESTION, so `#13335`/`#10193` are NOT
#: preconditions of this entry. They are preconditions of serving these labels
#: from the SQL VIEW — the two-value fold — which is the path a lane would take if
#: it tried to answer the cage in SQL. This leg takes the fold instead, which is
#: why the gate closes here and those two issues stay open on their own merits.
#: `docs/architecture/SEARCH-ARCHITECTURE.md` carries the same correction.
#:
#: WHAT IT COSTS, named rather than implied: two tenant-scoped record reads
#: (the label's rows, then its status-bearing journal events) before the kNN — THREE
#: data statements where the column arm issues two. Two consequences, both taken
#: deliberately rather than inherited:
#:
#:   * **the shortfall `count(*)` is not issued.** The prefetch already returns the
#:     exact `available` figure (:func:`_folded_terminal_keys` returns both), so a
#:     fourth statement would re-ask a question this leg has answered.
#:   * **the read budget is divided by FOUR, not by
#:     :data:`RECORD_LEG_STATEMENTS_PER_READ`.** The divisor counts the statements
#:     the leg may issue PLUS the one share it must leave for the graph branch; with
#:     three statements a divisor of three would let this leg spend the whole read
#:     and hand the graph a floored `1 ms` — the `#11691` reserve, which is exactly
#:     what `record_statement_ms`'s ``statements`` argument exists for.
#:
#: ⛔ AND THE STATEMENT BOUND DOES NOT REACH THE FOLD'S PYTHON HALF — which is
#: why this module bounds it itself. ``statement_timeout`` is a SERVER-side
#: per-statement bound (`record_serve.open_record` applies ``min(
#: RECORD_STATEMENT_TIMEOUT_MS, record_statement_ms)``); ``node_from_row`` per row
#: and ``apply_event_stream_folds`` over every event run in the CLIENT process,
#: where nothing can cancel them and the caller has no outer wall clock
#: (``search_engine`` reads ``remaining_read_ms`` only after this leg returns). So
#: the prefetch is bounded by two row budgets
#: (:data:`_FOLDED_PREFETCH_ROW_BUDGET`, :data:`_FOLDED_PREFETCH_EVENT_BUDGET`):
#: past either, :func:`_folded_terminal_keys` returns ``None`` and the leg raises
#: :class:`RecordVectorDeclined`, so the caller NAMES the fallback and its graph
#: branch applies the identical cage in Cypher — while a TRUNCATED fold would have
#: served the terminal keys it dropped.
#:
#: ⚠️ ONE COST THE BUDGETS DO NOT REMOVE: ``read_lifecycle_events`` has no
#: ``target`` here, so it reads the TENANT's whole status-bearing journal subset,
#: not this label's — reading ``object`` parses the ``subject`` birth records too.
#: Materialising the fold, so the prefetch disappears, is the follow-up.
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
    # ⛔ `object`/`subject` carry NO `updated_at` column (`table_columns()`), so
    # the recency half is `created_at` alone. It is placed in the UPDATED slot on
    # purpose: `_recency_sql` renders that slot as ``coalesce(t.created_at, '')``,
    # which is exactly what `search_engine.recency_key_expr`'s CASE degrades to
    # when `updatedAt` is absent — the same key the graph leg hands the caller's
    # ONE sort. Writing it in the CREATED slot instead would emit a bare
    # literal from `_recency_sql`'s ``updated is None`` arm, i.e. no recency at
    # all, and every Object would sort as oldest.
    "object": ("object", "id", "", ("created_at", None)),
    "subject": ("subject", "id", "", ("created_at", None)),
}


#: The arming surface for the CUTOVER, default OFF.
#:
#: ⛔ WHY THIS EXISTS, and it is a measured gap rather than caution (#11660
#: review round 3, P1). The record is a journal fold with NO backfill, so it
#: holds a vector only for content written AFTER the move is deployed. A
#: cutover that made the record the dense leg's only source would therefore DROP
#: every pre-existing point out of the dense pool — silently, with the trace
#: reporting `mechanism=pgvector, reason=ok, degraded=False`, because the
#: shortfall assertion counts `available` with the record's own predicate and so
#: cannot see material that only the graph holds. That is `#5090` Q2 ("nothing is
#: silently invisible") on the branch that would carry production traffic.
#:
#: The infrastructure this module is — the column, the index, the reader, the
#: residual scan — is complete and measured. What is NOT done is getting the
#: existing vectors INTO the record, and until that exists the graph is the only
#: source that holds the whole corpus, so it stays the source. Arming this is a
#: deliberate act, taken when the record's coverage is known — the same posture
#: `TORTOISE_EMBED_GATE` and `record_serve.SERVE_MODE` take, and for the same
#: reason: a correctness change to the read path is measured before it is trusted.
#:
#: ⚠️ Left off, NOTHING regresses: the graph branches answer exactly as they did
#: before this change (they are the fallback they always were), AND the in-graph
#: vector index is not retired either — an unarmed store keeps an index it has
#: and still gains one it lacks, which is `main`'s posture (#11660 review round
#: 7, P2-2: the arm gate governs the CREATE as well as the DROP, so a store can
#: never be left with neither). What arming changes is that the index leaves
#: once the record answers the dense read. Getting the existing corpus into the
#: record is the thing to price before arming, and it is the second reason this
#: is a switch rather than a default: a store that arms it needs the record
#: backfilled FIRST, not after.
ARMING_ENV = "TORTOISE_RECORD_VECTOR_LEG"

#: How many data statements the COLUMN-FILTERED arm actually ISSUES — the kNN
#: read, and on the SHORT path the shortfall ``count(*)``.
#: ⛔ A WRITTEN LITERAL ON PURPOSE, for the reason :data:`FOLD_ARM_STATEMENTS`
#: gives below: the dependency runs FROM this count TO the divisor, never the
#: other way, so a pin can red when the two drift instead of deriving both from
#: one declaration (``#13466`` cycle 5, P2).
COLUMN_ARM_STATEMENTS = 2

#: How many SHARES the COLUMN-FILTERED arm divides the read's budget into — its
#: TWO data statements (the kNN read, and on the SHORT path the shortfall
#: ``count(*)``) plus the ONE share left behind for the graph branch — and
#: therefore ITS divisor (#11691). ⛔ The divisor is NOT the statement count: this
#: arm ISSUES two and divides by three, for the same reason the folded arm issues
#: three and divides by :data:`FOLD_ARM_DIVISOR` (four) — conflating the two is
#: the defect that pair of constants exists to stop. ⛔ NOT the folded arm's
#: either: `object`/`subject` issue :data:`FOLD_ARM_STATEMENTS` (three) and divide
#: by :data:`FOLD_ARM_DIVISOR` (four) — see `vector_search`, which picks the
#: divisor per arm before any statement runs.
#:
#: ⛔ WHY THE LEG DOES NOT SIMPLY GET THE WHOLE BUDGET. ``statement_timeout``
#: bounds EACH STATEMENT, not the transaction (Postgres semantics), and this
#: COLUMN-FILTERED arm
#: may issue TWO of them: the kNN read, and — on the SHORT path, which is the
#: COMMON one, because a live tenant legitimately holds fewer rows than the limit
#: — the shortfall ``count(*)``. Handing each the read's full budget would let the
#: leg spend 2x it. And even ONE statement at the full budget is too much, for a
#: reason that is about the caller rather than the SQL: the fused read's collector
#: (`search_engine.degradation_chain`) takes only futures that are ALREADY done
#: when its own deadline expires and records the rest as ``reason="timeout"``,
#: discarding their rows. The record leg's timer starts at the STATEMENT, which is
#: strictly after the pool borrow, the connect and the two ``set_config`` round
#: trips — so a leg bound to the whole budget is cancelled AT the deadline, the
#: GRAPH branch that must answer after it has no time left, and the read still
#: pays a ``timeout`` with no rows. With a third of the budget per statement, two
#: statements spend at most two thirds and the remaining third is the reserve the
#: graph fallback is answered from — which is what makes the named
#: ``scan_fallback`` land INSIDE the read's budget instead of after it. ⚠️ That
#: reserve is HANDED OVER, not merely left on the clock: on a fall-through,
#: ``search_engine.run_vector_query`` queries the graph branches with
#: ``search_engine.remaining_read_ms(<the record leg's start>, read budget)``
#: rather than the whole budget again (#11691 review P2). Without that half the
#: division would buy nothing: a leg cancelled at a third of the read could then
#: spend the WHOLE budget in the graph branch and end past the collector's
#: deadline, which is the ``reason="timeout"``-with-no-rows outcome this
#: constant exists to prevent.
#:
#: ⛔ THIS DIVIDES, and the acceptance as written on #11691 says
#: ``min(<record door bound>, the read's own budget)`` — so the implemented bound is
#: STRICTER than the written one, deliberately, and the difference is stated here
#: rather than left for a reader to discover from the arithmetic (#11691 review,
#: P3). The consequence to know before diagnosing a fallback: a record leg that
#: would answer in 200–500 ms *inside* a 500 ms read is cancelled at ~166 ms, its
#: rows are discarded, and the trace records the SAME ``scan_fallback`` a genuinely
#: wedged store produces — so ``leg_trace.reason`` alone does NOT distinguish "too
#: slow for its share" from "the store did not answer". The whole-budget
#: alternative is not available (it is the defect this change fixes: the graph
#: fallback then misses its own deadline), so what a reader needs is the bound the
#: leg was given: it is ``min(RECORD_STATEMENT_TIMEOUT_MS,
#: record_statement_ms(read budget))`` — :func:`record_statement_ms` is that
#: division WITH its ``max(1, …)`` floor, and the floor is not decoration: written
#: as a bare ``read budget // RECORD_LEG_STATEMENTS_PER_READ`` the bound reads
#: ``0`` for a 1–2 ms budget, which Postgres takes as NO TIMEOUT AT ALL — the exact
#: trap this change exists to close, so a reader diagnosing a fallback must not be
#: sent to the unfloored form (#11691 review, P2). That bound is computable from
#: the read's configured budget, and it is NOT recorded in the trace today — that
#: gap is named here rather than implied to be observable.
RECORD_LEG_STATEMENTS_PER_READ = COLUMN_ARM_STATEMENTS + 1

#: How many data statements the folded arm actually ISSUES — its two prefetches
#: (the label's rows, then the tenant's status-bearing journal events) and the kNN.
#: ⛔ THIS IS A WRITTEN LITERAL ON PURPOSE, and the dependency runs FROM it TO
#: :data:`FOLD_ARM_DIVISOR` — never the other way. Spelled as
#: ``FOLD_ARM_DIVISOR - 1`` it was an EXPECTED VALUE DERIVED FROM THE SAME
#: DECLARATION AS THE ACTUAL ONE, so the reserve pin could not fail for any
#: divisor (`#13466` cycle 5, P2 — the third recurrence of this class): with
#: ``statements := divisor - 1`` the inequality
#: ``budget - share*statements >= share`` reduces to ``budget mod divisor >= 0``,
#: which is true for every value. A written ``3`` is a datum the source cannot
#: move, so `tests/test_13422_object_subject_dense_leg.py` can derive the REAL
#: count from the folded path's statement sites and red when the two disagree —
#: which is the drift a constant merely related to the divisor cannot see.
FOLD_ARM_STATEMENTS = 3

#: What the FOLDED arm DIVIDES the read by — one MORE than the
#: :data:`FOLD_ARM_STATEMENTS` it issues, giving the graph branch its reserve
#: share.
#: ⚠️ The divisor and the statement count are DIFFERENT numbers and conflating them
#: is the defect this pair of constants exists to stop (``#13466`` cycle 4, P3):
#: the folded arm ISSUES three data statements and therefore divides by FOUR,
#: because a divisor equal to the count spends the whole read and leaves nothing.
FOLD_ARM_DIVISOR = FOLD_ARM_STATEMENTS + 1


def record_statement_ms(read_budget_ms: int | None,
                        statements: int = RECORD_LEG_STATEMENTS_PER_READ,
                        ) -> int | None:
    """The per-statement bound for this leg inside a read of ``read_budget_ms``.

    ``None`` (an unbudgeted caller: health, replay, backup, restore — the reads
    that legitimately take seconds) stays ``None``, which leaves the door's own
    ceiling untouched. A budget is divided by ``statements`` and never rounds down
    to ``0``: Postgres reads ``statement_timeout = 0`` as NO TIMEOUT AT ALL, so the
    floor is 1 ms and :func:`tortoise.record_serve.open_record` clamps again on its
    side.

    ⛔ ``statements`` EXISTS SO THE RESERVE SURVIVES A LEG THAT ISSUES MORE THAN TWO,
    and it is not a tuning knob (`#13466` review P3). The constant counts the DATA
    statements a leg may issue *and the one share it must leave behind*: a
    column-filtered label issues two (the kNN read, the shortfall ``count(*)``) and
    a folded one issues three (its two prefetches, then the kNN), so passing the
    raw statement count would let the folded leg spend the WHOLE read and hand the
    graph branch a floored ``1 ms`` — turning the label's real path into an empty
    result on exactly the labels whose graph branch is a brute-force scan. The
    folded arm therefore divides by its count + 1.
    """
    if read_budget_ms is None:
        return None
    return max(1, int(read_budget_ms) // max(1, int(statements)))


class RecordVectorError(RuntimeError):
    """The record could not answer the dense leg. NOT an absence."""


class RecordVectorDeclined(RecordVectorError):
    """The record DECLINED this read — a bound, not a failure and not an absence.

    ⛔ RAISED RATHER THAN RETURNED AS ``None`` (`#13466` review P3). ``None`` is the
    contract's *"this label is not served from the record at all"*, and the caller's
    two consequences are OPPOSITE for the two cases: a not-served label falls
    through SILENTLY (its graph branch is its real path and always was), while a
    DECLINED leg has already spent this read's prefetch and must report the
    fallback (`#4999`: *a fallback must report the fallback*) and hand the graph
    branch only the remainder of the budget (`#11691`). Returned as ``None``, the
    decline was recorded as a plain ``scan`` — indistinguishable from "no record
    serves this label" — which is the one trace signal an operator would use to
    see the bound firing, and it gave the graph the full budget again.

    Subclassing :class:`RecordVectorError` is deliberate: an unaware caller keeps
    the correct behaviour (take the graph branch), and only a caller that wants to
    tell the two apart has to look.
    """


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


#: The sentinel `_STATUS_COLUMN` uses for a label whose status is NOT a column on
#: its record row. A record table cannot hold it (§2.6: the value is a
#: projection-owned CACHE, and `chk_object_props_no_derived` refuses it in the
#: props tail), so the cage is applied from the record's own journal fold instead
#: of from SQL. ⛔ It is a VALUE, never a column name, so a lane that renames a
#: column cannot collide with it; the leading NUL makes `sql` refuse it outright
#: if it ever reaches a statement.
_FOLDED_STATUS = "\x00folded"

#: ``entity_type -> (status column, carries the legacy `outdated` flag)`` for the
#: labels whose dense leg must not leak TERMINAL rows (`#689`).
#:
#: ⛔ THIS IS NOT A SECOND VOCABULARY. The values come from
#: `search_engine._status_vocab_for`, the ONE family→vocabulary dispatcher, at
#: call time; this map only says WHERE the status lives on each label and whether
#: the legacy `outdated` conjunct applies — facts about the SCHEMA, which the
#: record owns. `source`/`event` are absent because they carry no status on the
#: dense leg today: `search_engine._STATUS_FILTERED_LABELS` is
#: ``("Point", "Object", "Subject")``, and `Source`/`Event` are not in it — so
#: adding them here would filter rows the graph leg still returns.
#:
#: ⛔ `object`/`subject` ANSWER :data:`_FOLDED_STATUS`, WHICH IS NOT A COLUMN, and
#: the distinction is the whole of `#13422`. Their status is a projection-owned
#: cache the schema refuses as a column (§2.6), so it is not readable from the
#: row; it is derived by replaying the record's journal through the ONE fold
#: (`_folded_terminal_keys`). A label whose status IS a column keeps the cheap
#: SQL predicate; a label whose status is folded pays two prefetch reads for the
#: cage. Both apply the SAME vocabulary and the same exclusion, which is what
#: `tests/test_13422_object_subject_dense_leg.py` pins structurally: every
#: label in `search_engine._STATUS_FILTERED_LABELS` has an entry here, so a family
#: added to that tuple cannot silently skip the exclusion on the record leg.
#:
#: ⛔ THE FOLDED ARM'S POPULATION IS THE FOLD'S POPULATION, BOUNDS INCLUDED — and
#: its bounds are NAMED, not assumed away (`#13466` cycle 3, P2). The one-home rule
#: requires this leg to consume `apply_event_stream_folds` rather than invent a
#: second fold, so wherever that fold and the live graph fold disagree, this leg
#: answers with the fold. Those disagreements are RECORDED as open in that home —
#: cited by SYMBOL and not by line, because a line number in a 7k-line file that
#: this leg does not own re-points at unrelated prose on the next insertion above it
#: (the repo's cite-by-symbol rule, `#10524`): the ``EVENT_STREAM_FOLDS`` docstring's
#: ``(a-2)`` clause (a delete-then-re-create ordering answers `absent`/`live` where
#: the graph holds `archived`) and its ``(b)``/``(c)`` clauses (the opposite,
#: conservative direction), each measured there over an exhaustive sweep. So
#: the honest statement is NOT "the record can never serve a row the graph hides":
#: it is that the record leg serves exactly the fold's answer, and the fold has a
#: named, bounded, pre-existing ordering gap owned on `#9974`. ⚠️ That gap is
#: NOT introduced here and is not this leg's to close — closing it in `record_vector`
#: would be the second declaration the rule forbids — but a reader must not infer a
#: guarantee this change did not make. Today nothing ships it: `cutover_armed()` is
#: OFF, so the graph answers. See the comment relayed to `#9974`.
_STATUS_COLUMN: dict[str, tuple[str, bool]] = {
    "point": ("status", True),
    "operator": ("status", True),
    "object": (_FOLDED_STATUS, False),
    "subject": (_FOLDED_STATUS, False),
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

    ⛔ THIS IS ONLY THE COLUMN ARM. A label whose status is NOT a column
    (``object``/``subject``, :data:`_FOLDED_STATUS`) cannot be filtered here and
    is filtered by :func:`_folded_terminal_keys` instead — the clause for it
    names the fold's own answer rather than a column, so emitting an empty list
    here is correct and is NOT "no cage". Calling this for a folded label and
    reading the empty answer as "nothing to exclude" is the `#689` regression
    this split exists to prevent.
    """
    mapping = _STATUS_COLUMN.get(entity_type)
    if mapping is None or excluded_statuses == ():
        return []
    column, has_outdated_flag = mapping
    if column == _FOLDED_STATUS:
        # Not expressible as a column predicate — see the docstring. The folded
        # arm is applied by the caller, not by this function.
        return []
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


#: How many folded terminal keys one dense read will bind into its statement.
#:
#: ⛔ A BOUND, NOT A QUALITY KNOB, and it fails toward the LEG THAT ALWAYS ANSWERED.
#: The folded arm passes the record's own terminal keys as ``<> ALL(%s)``; a tenant
#: whose fold calls an enormous number of Objects terminal would make that array
#: the dominant cost of the read's statement. Past this bound the leg DECLINES its
#: turn (:class:`RecordVectorDeclined`, which the caller answers from the graph
#: with the identical cage applied in Cypher) — so the bound itself can neither
#: hide a live row nor serve a terminal one: it only changes WHICH LEG answers, and
#: the graph leg's cage is the live fold's. (What the record CAN serve is the FOLD's
#: answer, whose own ordering bounds are named at :data:`_STATUS_COLUMN` and owned
#: on `#9974` — that is a property of the one home, not of this bound.)
#: Materialising the fold, which makes the list disappear, is what removes the bound.
#:
#: ⚠️ WHEN IT CAN FIRE, stated because the first cut of this constant was justified
#: by a cost that was not this one (`#13466` review P2). The prefetch reads the
#: label's rows, so reaching 4,000 terminal keys means ~4,000 rows in this tenant,
#: and the bound is therefore reachable only while the PREFETCH fits its statement
#: share — which is why that prefetch selects every column EXCEPT the two payload
#: ones (:func:`_fold_prefetch_select`). MEASURED on 8,000 `object` rows: `SELECT *`
#: took **~2.0 s** and the payload-free read took **~0.19 s** (≈47 ms of it the
#: SELECT, the rest `node_from_row` + `apply_event_stream_folds`), against the
#: ~0.125 s share a 500 ms read gives three folded statements plus the graph's
#: reserve. So the bound is the operative guard it claims to be at this scale,
#: while `SELECT *` made it unreachable — the statement was cancelled first, and the
#: leg reported a timeout while presenting a bound it could never fire.
#:
#: ⛔ THE RESIDUAL, named rather than implied, and named CORRECTLY (`#13466` review
#: cycle 2 P3, then cycle 4 P3 — the first statement of it was wrong in its
#: mechanism, and the second named only HALF of its observable outcomes):
#: `statement_timeout` bounds STATEMENTS, `record_statement_ms` divides the read
#: among them, and the fold runs BETWEEN them where that arithmetic does not reach.
#: Its Python work is therefore bounded by ROW AND EVENT COUNTS
#: (:data:`_FOLDED_PREFETCH_ROW_BUDGET`, :data:`_FOLDED_PREFETCH_EVENT_BUDGET`),
#: because a count is the only thing this module can enforce across statements.
#:
#: ⛔ AN OVERRUN THEREFORE HAS **TWO** OBSERVABLE OUTCOMES, and naming one is how a
#: diagnoser is sent to a trace they will never see:
#:   * past a declared BUDGET — a prefetch row/event count
#:     (:data:`_FOLDED_PREFETCH_ROW_BUDGET`, :data:`_FOLDED_PREFETCH_EVENT_BUDGET`)
#:     or the fold's terminal-key count (:data:`_FOLDED_TERMINAL_KEY_BUDGET`) — the
#:     leg RAISES :class:`RecordVectorDeclined`, so the caller takes its NAMED
#:     `scan_fallback` arm with `remaining_read_ms`. Measured on 8,000 objects with
#:     4,001 archive events: 215 ms warm / 619 ms cold, the `#4999` surface;
#:   * ⚠️ UNDER every budget the fold is still not a clock: a tenant whose prefetch
#:     is inside both counts but whose fold is slow for another reason (a
#:     pathological props payload, a slow `node_from_row`, a busy host) overruns
#:     what is LEFT of the read, and then nothing raises — the fused read's
#:     collector (`search_engine.degradation_chain`) expires on its own deadline and
#:     DISCARDS the rows as `timeout`, which is the `#11691` outcome the divisor
#:     work exists to prevent, one layer up, and the one with NO `scan_fallback` and
#:     no trace entry of its own to point at.
#: Both end in the graph branch; they differ in what the trace says, which is the
#: whole reason this paragraph enumerates instead of summarising.
#:
#: At the measured 0.19 s for 8,000 rows inside a 500 ms read the leg ANSWERS; at
#: 10,000 rows it declines. Materialising the fold is what removes both the cost and
#: the bound — the follow-up that makes the prefetch and these constants disappear.
#: Filed rather than attempted here: a per-target narrowing of the node read is
#: exactly the place where a missed path becomes a `#689` LEAK, and
#: correctness-before-cost is the order this lane was given.
_FOLDED_TERMINAL_KEY_BUDGET = 4000

#: How many of the label's ROWS one dense read will prefetch for the fold.
#:
#: ⛔ IT BOUNDS THE HALF `statement_timeout` CANNOT REACH. `_folded_terminal_keys`
#: reads every row of the label for the tenant and runs `node_from_row` over each in
#: the CLIENT process — the per-statement bound does not reach between statements,
#: so nothing else in this module bounds that work (the residual at
#: :data:`_FOLDED_TERMINAL_KEY_BUDGET` states the mechanism). Past this many rows
#: the leg DECLINES (`RecordVectorDeclined`), so the caller NAMES the fallback and
#: its graph branch answers with the graph's own cage — CORRECT not degraded, and
#: ⛔ NOT a claim that the two legs agree row-for-row: the leg serves the FOLD's
#: answer, and the fold's recorded ordering bound (`cache_projection`'s `(a-2)`,
#: reachable through a delete-then-re-create under an explicit id) can answer LIVE
#: where the graph answers terminal. That divergence PRE-EXISTS this change and is
#: #9974's, not this bound's business.
#:
#: ⚠️ AND DECLINING IS NOT THE SAME AS TRUNCATING. A trimmed row set would silently
#: drop terminal keys, so the leg would serve rows the FOLD calls terminal — the
#: `#689` fail-OPEN direction. Declining serves nothing from the record at all.
#:
#: The number is tied to the measurement above rather than picked: 10,000 rows is
#: already ~0.18 s of unfoldable Python inside a read whose folded arm gets ~0.125 s
#: per statement, so a tenant past it is not record-served at fleet scale anyway
#: (~0.19 s for 8,000 rows, measured) — this bound only makes the answer NAMED and
#: bounded instead of a cancellation somewhere downstream. Materialising the fold is
#: what removes it.
_FOLDED_PREFETCH_ROW_BUDGET = 10_000

#: How many JOURNAL EVENTS one dense read will replay for the fold.
#:
#: The prefetch's second read is the tenant's status-bearing journal subset
#: (`read_lifecycle_events` with no `target`), and `apply_event_stream_folds`
#: builds two dicts over it before folding — again in Python, again uncancellable.
#: Per-event work is a dict insert rather than a row projection, so this bound is
#: an order of magnitude looser than the row one; past it the leg declines for the
#: same reason, and with the same fail-safe direction, as the row budget above.
_FOLDED_PREFETCH_EVENT_BUDGET = 50_000


#: The column names dropped from the fold's prefetch: the record's STORED payloads.
#:
#: ⛔ NOT A THIRD DECLARATION — both are read from the homes that already declare
#: them. `stored_form.STORED_VECTOR_PROPS` is the set of STORED vector properties,
#: and `cache_projection.GENERATED_COLUMNS` is the generated-column set (the
#: `embedding_vec` the ONE converter derives). `#13466` review cycle 2 (P3) caught
#: the first cut hardcoding both names while its own docstring claimed it was
#: reading those declarations: a vector prop added to either home would have been
#: re-selected here (cost) with nothing going red.
def _fold_prefetch_dropped() -> frozenset[str]:
    """The columns the fold never reads — declared by the modules that own them."""
    from .cache_projection import GENERATED_COLUMNS
    from .stored_form import STORED_VECTOR_PROPS

    return frozenset(STORED_VECTOR_PROPS) | frozenset(GENERATED_COLUMNS)


def _fold_prefetch_select(table: str) -> str:
    """`t.col, t.col, …` for `table` — every column EXCEPT the stored payloads.

    ⛔ WHY THE PAYLOAD IS DROPPED, measured (`#13466` review P2): the fold reads
    `id`/`name`/kind/`superseded_by`/`superseded_at`, the NULL-ness of
    `embedding_vec`, and `props` — the jsonb `node_from_row` genuinely needs, so
    it is NOT dropped here. What IS dropped, and dominates the row, are the two
    VECTOR payloads (`embedding` and `embedding_vec`). `SELECT *` materialises
    both, and on 8,000 rows it measured **~2.0 s** against **~0.19 s** for this
    projection. Per-row sizes, measured with `pg_column_size` on `pgvector/pgvector:pg16`
    (`#13466` review cycle 2): `embedding_vec` is a FIXED `vector(384)` = **1,544 B**
    (it is a fixed-width type, so it cannot be larger), and `embedding` jsonb for a
    384-element array is **4.6–7.7 KB** depending on whether the floats are
    short-repr or full-precision. Both dwarf every column the fold does read.

    ⚠️ NOTHING IS SILENTLY LOST BY THE NARROWER ROW. `cache_projection.projected_columns`
    excludes these two for both labels, so `_props_from_row` never projected them in
    the first place — the `RecordNode`s here are byte-identical to the ones
    `SELECT *` produced (verified in `#13466` cycle 2: keys equal, props identical,
    no prop gained or lost). A missing payload column therefore cannot change a fold
    OUTCOME, which is what makes this a cost fix rather than a behaviour change.

    ⛔ RE-DERIVED PER CALL FROM THE CENSUS, deliberately UNCACHED here.
    `record_projection.table_columns()` is itself cached and invalidated on the
    migrations' fingerprint (~0.5 ms per call including its own copy), and a
    process-lifetime cache in THIS module would defeat exactly that invalidation: a
    column added by a migration mid-process would be silently absent from the fold's
    props, and a dropped one would render a statement that errors on every read until
    restart (`#13466` review cycle 2, P3 — demonstrated against the cached first cut).
    A sub-millisecond re-derivation against a leg that spends tens of milliseconds is
    the right side of that trade.
    """
    from .record_projection import table_columns

    dropped = _fold_prefetch_dropped()
    return ", ".join(f"t.{column}"
                     for column in sorted(table_columns()[table] - dropped))


def _rows_as_dicts(cur: Any) -> list[dict[str, Any]]:
    """The rows a cursor just fetched, as dicts by column name.

    ⛔ NOT `cache_projection._rows`: that is a private of a module this one
    already imports lazily for the fold, and reaching into it would couple two
    modules' internals for a `zip`. The shape, not the function, is what both
    need.

    ⛔ AND IT TAKES NO STATEMENT — the `execute` stays at the CALL SITE, with the
    statement written there. That is not style: `tools/cypher_inventory.py`
    classifies an `execute` by resolving its first argument, so a shared helper
    taking `sql` as a parameter makes every call site an **unresolvable door
    call** — counted in the Cypher census's `unresolved` bucket, which is where a
    migration-candidate is looked for. A Postgres `SELECT` recorded as an
    unclassifiable CYPHER statement is a false row in exactly the document that
    exists to decide what may move off the graph. Written at the call site, the
    same statement resolves and lands in the `non_cypher_door_calls` bucket that
    exists for it.
    """
    columns = [d[0] for d in (cur.description or ())]
    return [dict(zip(columns, row, strict=False)) for row in cur.fetchall()]


def _folded_terminal_keys(conn: Any, cur: Any, scope: _Scope, entity_type: str,
                          excluded_statuses: tuple | None,
                          ) -> tuple[list[str], int] | None:
    """The keys this label's RECORD fold calls TERMINAL, and how much it has.

    ⛔ THE CAGE FOR A LABEL WHOSE STATUS IS NOT A COLUMN (`#13422`). `object` and
    `subject` cannot answer :func:`_status_predicates` — the record refuses
    `status` as a column (§2.6, `chk_object_props_no_derived`) and the SQL view
    that folds it knows only `live`/`superseded` (`#13335`). Returning an empty
    predicate for them and calling the cage applied is the `#689` regression the
    whole gate exists to prevent, so the leg applies it from the fold instead.

    WHY THIS IS NOT A SECOND DECLARATION OF THE FOLD. Nothing here restates the
    rule. The rows become `RecordNode`s through `cache_projection.node_from_row`
    — the SAME row→property map both record doors read, which is also where
    `DERIVED_FROM_RECORD`'s carrier fold (`superseded_by`/`superseded_at` →
    `superseded`) is applied — and `cache_projection.apply_event_stream_folds`
    replays the record's journal over them through `status_folds`' vocabulary and
    the live fold's own per-event guard. So the four terminal words come from the
    ONE home, the sequencing comes from the record's `seq`, and a change to the
    fold reaches this leg without an edit here.

    WHICH WORDS ARE "TERMINAL" is still `search_engine._status_vocab_for`, the
    ONE family→vocabulary dispatcher, and ``excluded_statuses`` is the caller's
    opt-in exactly as it is on the graph branches (``()`` = terminal rows IN,
    which yields an EMPTY key list because the answer is "exclude nothing").
    ⚠️ The comparison is EXACT and case-sensitive ON PURPOSE: the graph clauses
    are ``coalesce(n.status, '') NOT IN ('archived', …)``, which a stored
    `'Archived'` survives, so a case-folding comparison here would hide a name the
    graph leg still returns — the "hide-a-live-name" direction `#9974` review
    round 7 refused.

    ⚠️ THE KEY THIS RETURNS IS THE LEG'S OWN `id` COLUMN — ``LABEL_SERVED``\'s
    second slot — not the record's ``node_key``. For `object`/`subject` the
    contract key `node_from_row` folds by is `id` (`cache_projection.KEY_PROPERTY`),
    the same column the kNN SELECTs and the same value the caller joins back to the
    graph with, so a list built from `node_key` (= `name`) would name the wrong
    side of the row and exclude NOTHING while looking correct. Pinned by
    `tests/test_13422_object_subject_dense_leg.py::test_the_folded_keys_are_the_columns_the_leg_returns`.

    ⚠️ A row whose `id` is NULL or empty is SKIPPED here rather than raising: the
    kNN's own post-filter already drops such a row (`a NULL key is not addressable
    by the caller`), so it can neither be served nor excluded, and letting
    `node_from_row` raise on it would turn one legacy row into a `RecordVectorError`
    for the whole label — a graph fallback caused by a row the leg was never going
    to return.

    Returns ``(keys, available)`` where ``available`` is how many non-terminal
    rows of this label carry a usable vector. That is the number the shortfall
    assertion needs, and it is EXACT rather than a second `count(*)` over the same
    predicate — which is why these two labels spend their three statement shares
    on (rows, events, kNN) and need no fourth.

    ⛔ AND IT RETURNS ``None`` WHEN EITHER PREFETCH IS OVER ITS BUDGET
    (:data:`_FOLDED_PREFETCH_ROW_BUDGET`, :data:`_FOLDED_PREFETCH_EVENT_BUDGET`).
    ``None`` is this leg's "not served HERE": the caller answers from the graph
    branch, which applies the identical cage in Cypher. ⚠️ The only alternative —
    TRUNCATING the prefetch — is the `#689` fail-OPEN direction: a row or event
    left out of the fold is a terminal key this leg would then SERVE. So the
    read declines instead, and the ``LIMIT`` below is ``budget + 1`` precisely so
    "exactly at the budget" and "one past it" are distinguishable.
    """
    from .cache_projection import (
        LifecycleEventStreamTruncated,
        apply_event_stream_folds,
        node_from_row,
        read_lifecycle_events,
    )
    from .live import is_position_ended
    from .search_engine import _status_vocab_for

    label = entity_type.capitalize()
    table, id_column = LABEL_SERVED[entity_type][0], LABEL_SERVED[entity_type][1]
    # `embedding_vec` is a pgvector `vector`, which psycopg2 hands back as an
    # unregistered (string) type. The question here is only NULL-ness, so it is
    # asked in SQL and never parsed in Python — a caster this module does not own
    # is not worth depending on for a boolean.
    cur.execute(
        f"SELECT {_fold_prefetch_select(table)}, "
        f"       (t.embedding_vec IS NOT NULL) AS has_embedding_vec "
        f"FROM public.{table} AS t "
        f"WHERE t.org_id = %s AND t.graph_id = %s "
        f"  AND t.{id_column} IS NOT NULL AND t.{id_column} <> '' "
        f"LIMIT %s",
        (scope.org_id, scope.graph_id, _FOLDED_PREFETCH_ROW_BUDGET + 1),
    )
    rows = _rows_as_dicts(cur)
    if len(rows) > _FOLDED_PREFETCH_ROW_BUDGET:
        # See _FOLDED_PREFETCH_ROW_BUDGET: a bounded decline, never a truncated
        # fold (a dropped row is a terminal key this leg would serve).
        return None
    # ⛔ THE PROBE COLUMN IS POPPED BEFORE THE PROJECTION. `node_from_row` refuses
    # an unclassified column by design
    # (`object.has_embedding_vec is not classified by the placement table, not
    # refused by the schema, and not record bookkeeping`) — so handing it the raw
    # row makes the whole label raise `UnknownRecordColumn`, i.e. a graph fallback
    # for these two labels on EVERY read. The datum is read here, in Python, and the
    # row the projection sees is the record's own.
    has_vector = {row.get(id_column): bool(row.pop("has_embedding_vec"))
                  for row in rows}
    nodes = [node_from_row(label, row) for row in rows]
    try:
        events = read_lifecycle_events(conn, org_id=scope.org_id,
                                       graph_id=scope.graph_id,
                                       limit=_FOLDED_PREFETCH_EVENT_BUDGET + 1)
    except LifecycleEventStreamTruncated:
        # Same bound, same direction: the replay is Python and uncancellable, and
        # a truncated stream would leave terminal folds unapplied.
        # ⛔ THE OVERFLOW IS THE READER'S TO REPORT, and it has to be: the LIMIT
        # bounds ROWS while the parse loop DROPS rows, so `len(events)`
        # under-counts the stream by the number of drops and a comparison HERE
        # cannot witness truncation once one row is skipped (measured: 50,001
        # raw rows + one unparseable => 50,000 events => guard silent, 10,000
        # events never seen). Comparing the count here was the fail-OPEN bug.
        return None
    folded = apply_event_stream_folds(nodes, events)
    vocabulary, _include_outdated = _status_vocab_for(label)
    # ⛔ `excluded_statuses == ()` IS NOT "use the default". It is the caller's
    # terminal-rows-IN opt (the `#1353` supersede-structure surface), and the graph
    # branches honour it by emitting NO clause. Written as
    # `frozenset(excluded_statuses or vocabulary)` the empty tuple is FALSY and
    # silently becomes the whole vocabulary — i.e. the one caller that asked to SEE
    # terminal rows gets them filtered out. Measured by
    # `test_the_audit_opt_in_returns_terminal_rows_on_the_record_leg`.
    excluded = (frozenset() if excluded_statuses == ()
                else frozenset(excluded_statuses or vocabulary))

    # ⛔ THE CAGE IS TWO AXES, NOT THE STATUS WORD ALONE (`#13034`, design §6).
    # Both graph dense branches cage with `search_engine._exclude_status_clause`,
    # which delegates to `live._terminal_excluded` — and that predicate excludes a
    # row whose STATUS word is terminal **and** a row the POSITION axis records as
    # ended (`isClosed`, or a `terminal` pipeline slot). Its own docstring forbids
    # a reader opting out of the position axis: "a reader may therefore pick its
    # vocabulary and its flag conjunct; it may not opt out of the position axis".
    #
    # A status-only cage here therefore SERVES an `:Object` ended only by
    # `isClosed` (`status` absent or `live`, the flag set) that the graph leg
    # HIDES — the `#689` fail-OPEN direction, silently, and it reads as better
    # recall. The connector/pack stage fold writes that marker, and
    # `apply_event_stream_folds` above has ALREADY put both props on `node.props`
    # (`pipeline.stage_move_props` is the one spelling of `STAGE_PROPS`), so the
    # datum is here and `public.object` needing no `is_closed` column is not a gap.
    # It also inflated `available`, so the shortfall assertion was measured against
    # an uncaged population.
    #
    # ⚠️ `excluded` empty is the caller's TERMINAL-ROWS-IN opt-in, and the graph
    # honours it by emitting NO clause at all (`_exclude_status_clause` returns ""
    # for a falsy vocabulary, taking the position axis with it) — so the whole cage
    # is gated on it, not just the status test.
    keys: list[str] = []
    available = 0
    for node in folded:
        if excluded:
            status = node.props.get("status")
            if isinstance(status, str) and status in excluded:
                keys.append(node.key)
                continue
            if is_position_ended(
                    is_closed=node.props.get("isClosed"),
                    stage_slot=node.props.get("pipelineSlot")):
                keys.append(node.key)
                continue
        if has_vector.get(node.key):
            available += 1
    return keys, available


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


def cutover_armed() -> bool:
    """Is the dense leg's CUTOVER armed? Default NO — see :data:`ARMING_ENV`.

    ⛔ PUBLIC, and a separate question from :func:`armed`, because one consumer
    outside this module must ask it: `projection.vector_index_retired()` gates
    both the `#4997` log LEVEL and the RETIREMENT itself on whether a replacement
    actually answers the dense read. A DSN alone stopped being that answer when
    this switch was added (#11660 review round 6, P1-3), and the retirement's
    dependence on the same answer is round 4's P1: dropping the in-graph index
    while nothing replaces it leaves the store with NEITHER an ANN index NOR a
    record leg — every dense read a brute-force `:Point` scan recording
    `scan_fallback`. One predicate, three consumers, no second opinion.
    """
    from .env_truthy import is_truthy

    return is_truthy(os.environ.get(ARMING_ENV))


#: Where this process's tenant journal lives — the SECOND input a coverage check
#: needs. `journal_coverage` reads the RECORD; the journal's own enumeration is
#: the caller's fact, and on the read path nobody holds it (the searcher has the
#: record's `(org_id, graph_id)`, never the cache graph name the journal path is
#: derived from). So the operator DECLARES it — the same declaration discipline
#: `TORTOISE_RECORD_SINGLE_TENANT` already takes, and for the same reason: a
#: wrong guess about which tenant this process serves would gate the wrong one.
#:
#: Unset is NOT an error and NOT a silent skip: the gate CANNOT answer without
#: it, so `journal_ready` returns "not enforced" and a WARNING is emitted once per
#: process naming exactly what is unchecked (see :func:`journal_ready`).
#:
#: ⛔ THE DECLARATION IS SUBJECT TO `#9192`'s RULE, and this module ENFORCES it
#: rather than repeating its name: a journal path is process-global, so it can
#: only describe ONE tenant. `journal_ready` therefore refuses to apply it unless
#: the deployment declared single tenancy
#: (`single_tenant_declared()`, `TORTOISE_RECORD_SINGLE_TENANT=1`) — on a process
#: serving several tenants the comparison would be tenant A's journal against
#: tenant B's rows, which `record_writer`'s own docstring calls "not a weaker
#: answer than the scope — it is a WRONG one, and it fails silently". The
#: direction is `#9192`'s: NOT READY (and one warning naming the declaration),
#: because a check that cannot be applied to this tenant must never report that
#: tenant as complete. The consequence is stated where it bites: an armed
#: MULTI-TENANT deployment keeps the graph branch until the journal can be
#: resolved PER SCOPE rather than from the environment (#13366).
RECORD_JOURNAL_ENV = "TORTOISE_RECORD_JOURNAL"

#: How long one tenant's readiness verdict is trusted. A coverage read is one
#: aggregate over `journal_event`, but this gate runs on EVERY read construction,
#: and a search must not pay a round trip it does not need. 30 s bounds how long
#: a record that has just fallen behind can still be served by the dense leg —
#: and a positive verdict is the only one that can be wrong in that direction
#: (a NEGATIVE verdict keeps the graph branch, which is always correct).
READINESS_TTL_SECONDS = 30.0

#: `(dsn, org, graph, journal) -> (checked_at, ready)`.
_READINESS: dict[tuple[str, str, str, str], tuple[float, bool]] = {}
_READINESS_LOCK = threading.Lock()


def _readiness_key(scope: _Scope, record_dsn: str | None) -> tuple[str, str, str, str] | None:
    """The cache key, or ``None`` when the DSN cannot be resolved.

    ⛔ A MALFORMED DSN IS "NOT READY", not an exception into the search path.
    `searcher` resolves the DSN before calling this, so the arm is unreachable
    from there — but this function is also reachable from the public
    :func:`journal_ready`, and the read path's rule is that a check which cannot
    run keeps the graph branch (it never turns a search into a 500).
    """
    # The DSN is keyed by its FINGERPRINT, never by value (#2903/#2796): this
    # dict is process-global and a raw DSN is secret material.
    from .record_writer import dsn_fingerprint
    from .record_writer import record_dsn as _resolve

    try:
        dsn = _resolve(record_dsn) or ""
    except Exception:
        return None
    return (dsn_fingerprint(dsn) if dsn else "", scope.org_id,
            scope.graph_id, os.environ.get(RECORD_JOURNAL_ENV, ""))


def reset_readiness_cache() -> None:
    """Forget every readiness verdict. For tests, and for an operator who has
    just completed a backfill and does not want to wait out the TTL."""
    with _READINESS_LOCK:
        _READINESS.clear()


def journal_ready(scope: _Scope, *, record_dsn: str | None = None,
                  now: float | None = None,
                  timeout_ms: int | None = None) -> bool:
    """Does the record hold this tenant's journal? The dense leg's gate (#13331).

    `ARMING_ENV` is a DEPLOYMENT switch; this is the per-tenant CONDITION that
    keeps it honest. Requirement 5 of `#13331`: *"do not arm the dense leg for a
    tenant whose backfill is incomplete."* The record is a fold of the journal,
    so a fold that is short of positions is a corpus that is short of memories
    — and the dense leg would answer from it with `mechanism=pgvector,
    reason=ok`, which is the silent-invisibility class `#5090` Q2 forbids.

    THE CHECK IS EXACT AND IT NEEDS BOTH SIDES: `journal_event`'s positions (the
    RECORD) against the journal's own enumeration (`EventLog.position()`), which
    is the number a replay numbers against. The read path holds only the
    record's `(org_id, graph_id)` — the journal path is derived from the CACHE
    graph's name, which this scope does not carry — so the operator DECLARES the
    path (:data:`RECORD_JOURNAL_ENV`), the same declaration discipline
    `TORTOISE_RECORD_SINGLE_TENANT` takes and for the same reason.

    ⛔ WITHOUT THE DECLARATION THE CHECK CANNOT RUN, and this is stated rather
    than papered over. The tempting record-only fallback is WRONG in both
    directions and is therefore not implemented:

    * requiring a contiguous prefix from 1 would refuse EVERY live-write tenant —
      a fresh graph is seeded at its journal's floor and legitimately starts at
      position N > 1 (`record_runner.read_checkpoint`'s `allocated` arm exists
      for exactly that shape), so the gate would disable the very population
      this issue is about;
    * accepting any contiguous suffix would never catch an incomplete backfill,
      which is the defect the gate exists for.

    So an undeclared journal returns ``True`` (the deployment switch decides, as
    it did before this issue) and emits ONE WARNING per process naming what is
    unchecked. The arming procedure makes the declaration part of the act: read
    `python -m tortoise record-reconcile --journal <path>` (it exits 3 with
    `verdict=unprojected_journal` while the record lags, and it REFUSES the leg
    with exit 2 rather than reporting a clean pass when the path cannot be
    counted), then declare the path AND `TORTOISE_RECORD_SINGLE_TENANT=1` for
    any tenant the leg is armed for — the second is what lets the declaration be
    applied at all (see below).

    FAIL-CLOSED once declared: an unreadable journal, an unreadable record, a
    missing dependency — every failure keeps the graph branch, which is a correct
    answer (`#11642` P1), and a check that cannot run must never be read as
    "ready".

    ⛔ THE MEASUREMENT IS BOUND BY THE READ'S OWN BUDGET (`timeout_ms`), because
    it runs on the REQUEST's thread *before* :func:`searcher` returns; an
    unbounded read here is the `#11691` defect re-opened one function above the
    fix for it. See :func:`_measure_readiness`.

    ⛔ AND THE DECLARATION MUST BE APPLICABLE TO THIS TENANT, or it is not
    applied (the `#9192` rule, ENFORCED here rather than cited): a
    process-global journal path describes ONE tenant, so on a process that did
    not declare single tenancy
    (:func:`tortoise.record_writer.single_tenant_declared`) the comparison would
    be one tenant's journal against another tenant's rows. That is not a weaker
    answer than the scope, it is a WRONG one, and it fails silently — so this
    returns ``False`` with ONE warning naming `TORTOISE_RECORD_SINGLE_TENANT`.
    The graph branch answers instead, which is `#11642` P1's correct answer,
    until the journal can be resolved per scope (#13366).

    ⛔ THE COUNT COMES FROM `EventLog.position_readonly()`, never from
    `EventLog.position()` (#13331 review F2): the latter takes the append lock
    and opens the journal for WRITING, so a read-shaped call on the request
    thread could suppress the `seq` of a concurrent live write for 30 s (the
    `_FLOCK_UNHEALTHY` amplification) and would need write permission on a
    journal a reader is legitimately mounting read-only. A journal path that
    does not EXIST is "not measured" (``None``), never "0 records, so nothing is
    missing" — the false PASS of review F3/B.
    """
    if not os.environ.get(RECORD_JOURNAL_ENV):
        _warn_readiness_undeclared()
        return True
    if not _journal_declaration_applies():
        return False
    clock = time.monotonic() if now is None else now
    key = _readiness_key(scope, record_dsn)
    if key is None:
        return False
    with _READINESS_LOCK:
        cached = _READINESS.get(key)
    if cached is not None and clock - cached[0] < READINESS_TTL_SECONDS:
        return cached[1]
    ready = _measure_readiness(scope, record_dsn=record_dsn,
                               timeout_ms=timeout_ms)
    with _READINESS_LOCK:
        _READINESS[key] = (clock, ready)
    return ready


#: One warning per process, not one per read.
_WARNED_UNDECLARED = False
_WARN_LOCK = threading.Lock()


def _warn_readiness_undeclared() -> None:
    global _WARNED_UNDECLARED
    with _WARN_LOCK:
        if _WARNED_UNDECLARED:
            return
        _WARNED_UNDECLARED = True
    logging.getLogger(LOG_NAME).warning(
        "record dense leg: %s is armed but %s is unset, so the PER-TENANT "
        "backfill-coverage gate cannot run — the leg will serve any tenant the "
        "deployment switch admits, including one whose record is short of "
        "journal positions (#13331 requirement 5, #5090 Q2). Declare the "
        "tenant's journal path and re-check with `python -m tortoise "
        "record-reconcile --journal <path>` before arming a tenant",
        ARMING_ENV, RECORD_JOURNAL_ENV)


def _journal_declaration_applies() -> bool:
    """May this process's journal declaration be applied to a tenant at all?

    ⛔ ONE PATH, PROCESS-GLOBAL, THEREFORE ONE TENANT (#9192). The env pair
    `TORTOISE_RECORD_ORG`/`TORTOISE_RECORD_GRAPH` is gated exactly this way by
    :func:`tortoise.record_writer.single_tenant_declared`, whose docstring states
    the reason verbatim: *"it can only be correct where the process serves one
    graph … on a process serving several tenants it is not a weaker answer than
    the scope — it is a WRONG one, and it fails silently."* `journal_ready`
    measures THIS tenant's rows against the journal the declaration names, so a
    declaration that does not apply to this tenant must not produce a verdict
    about it: ``False`` keeps the graph branch (a correct answer, `#11642` P1),
    while ``True`` would be a false PASS over a tenant nobody measured.

    Unset AND unimportable are the same answer (not applicable): this runs on
    the SEARCH path, where a missing arming module is not worth a raise.
    """
    try:
        from .record_writer import single_tenant_declared
    except Exception:  # noqa: BLE001, RUF100 — an unimportable arming module is "unarmed"
        return False
    try:
        applies = bool(single_tenant_declared())
    except Exception:  # noqa: BLE001, RUF100 — a raising declaration is not a declaration
        return False
    if not applies:
        _warn_readiness_multitenant()
    return applies


#: One warning per process for the multi-tenant refusal, like the undeclared one.
_WARNED_MULTITENANT = False

#: A THIRD absence, and the one an operator is most likely to cause: the journal
#: IS declared and the tenancy IS declared, but the path cannot be counted
#: (a typo, a reader deployment that does not mount the writer's volume). Keyed
#: by path so the message can name the one to fix.
_WARNED_UNMEASURABLE: set[str] = set()


#: A FOURTH absence: the journal was COUNTED, and the count contradicts the
#: record (it enumerates no records while the record holds rows). Keyed by path
#: like `_WARNED_UNMEASURABLE`, and reported for the same reason.
_WARNED_CONTRADICTORY: set[str] = set()


def _warn_readiness_unmeasurable(path: str | None) -> None:
    """Say ONCE PER PATH that a declared journal could not be counted.

    ⛔ WARNING, not DEBUG (#13331 review F2, point 3). The gate is CORRECTLY
    off — a check that cannot run is not ready — but this is the one failure the
    operator CAUSED, by declaring a path, and it was the quietest of the three:
    "nothing declared" warns (`_warn_readiness_undeclared`) and "cannot be
    attributed" warns (`_warn_readiness_multitenant`), so a typo in the path was
    the only way to disable the dense leg with nothing in the default log. The
    review priced exactly that: *"the dense leg is silently off for ever on that
    box with no operator-visible signal in the default log level"*. Once per
    path, because a read path must not log per read.
    """
    key = path or ""
    with _WARN_LOCK:
        if key in _WARNED_UNMEASURABLE:
            return
        _WARNED_UNMEASURABLE.add(key)
    logging.getLogger(LOG_NAME).warning(
        "record dense leg: %s declares %r, which this process cannot count — "
        "it is missing, unreadable, or not a regular file. An uncountable "
        "journal is NOT an empty one, so every tenant keeps the graph branch "
        "until the path is fixed (#13331, #5090 Q2). Reported once per path.",
        RECORD_JOURNAL_ENV, key)


def _warn_readiness_contradictory(path: str | None, cov) -> None:
    """Say ONCE PER PATH that a counted journal CONTRADICTS the record.

    ⛔ A LEG THAT IS CORRECTLY OFF MUST NOT BE SILENTLY OFF — the same rule
    `_warn_readiness_unmeasurable` states, for the shape one branch over
    (#13345 review P2). Here the journal WAS counted and the count cannot be the
    one behind this tenant's rows, so `JournalCoverage.unprojected` refuses to
    answer and the gate keeps the graph branch. Without this, declaring a
    rotated, replaced or freshly-truncated path would disable the dense leg for
    ever with nothing in the default log — the failure an operator caused
    THEMSELVES being the quietest one, which review F2 point 3 already priced.

    ⛔ THE REASON IS THE COVERAGE'S OWN (#13345 review F2), not a sentence
    re-spelled here. The first cut hardcoded "enumerates NO records", which was
    the whole vocabulary when the empty-journal shape was the only one guarded;
    the guard now refuses ANY record holding more positions than its journal
    enumerates (a rotated file that has since received a few lines), and a
    message that names the empty case for a 100-row record would send the
    operator looking for an empty file. Quoting the reading is also what
    `record_reconcile.log_result` does, so the log and the reconcile line cannot
    describe one contradiction in two ways.
    """
    key = path or ""
    with _WARN_LOCK:
        if key in _WARNED_CONTRADICTORY:
            return
        _WARNED_CONTRADICTORY.add(key)
    logging.getLogger(LOG_NAME).warning(
        "record dense leg: %s declares %r, which CONTRADICTS this tenant's "
        "record — %s. Its lag cannot be measured, so every tenant keeps the "
        "graph branch (a correct answer, #11642 P1) until the declaration "
        "names the right file (#13371, #13345). Reported once per path.",
        RECORD_JOURNAL_ENV, key, cov.unprojected_unknown_reason)


def _warn_readiness_multitenant() -> None:
    """Say ONCE PER PROCESS that one journal cannot serve several tenants.

    ⛔ IT NAMES THE DECLARATION IN PROSE AND NEVER READS IT (#9192's reader
    pin). This module must not touch the process-global tenant variables —
    `tests/test_9192_tenant_armed_by_request.py::
    test_the_env_pair_has_no_reader_outside_the_gated_resolver` allows those
    literals only inside `record_writer`'s gated resolver, and the earlier cut
    of this warning imported `RECORD_SINGLE_TENANT_ENV` to format its message.
    That import is a READER as far as the AST pin can tell, and it is the exact
    shape the pin exists to refuse (a copy of the read shape beside the gate).
    Naming the variable in an operator message is not a read — `cache_leg.py`
    already spells it the same way — and the only thing this function needs is
    the WORD the operator can search for, never the value.
    """
    global _WARNED_MULTITENANT
    with _WARN_LOCK:
        if _WARNED_MULTITENANT:
            return
        _WARNED_MULTITENANT = True

    logging.getLogger(LOG_NAME).warning(
        "record dense leg: %s names ONE journal, but this process did not "
        "declare single tenancy (TORTOISE_RECORD_SINGLE_TENANT is unset), so "
        "the per-tenant backfill "
        "coverage of EACH tenant cannot be measured — comparing one tenant's "
        "journal against another tenant's rows is a wrong answer, not a weak "
        "one (#9192). Every tenant on this process keeps the graph branch, "
        "which is a correct answer, until the journal is declared per tenant "
        "(#13366); set TORTOISE_RECORD_SINGLE_TENANT=1 ONLY in a process that "
        "serves one graph",
        RECORD_JOURNAL_ENV)


def _measure_readiness(scope: _Scope, *, record_dsn: str | None,
                       timeout_ms: int | None = None) -> bool:
    """The coverage read behind :func:`journal_ready`. Never raises.

    ⛔ ``present > 0`` IS PART OF THE ANSWER, not a formality: `contiguous` is
    true for an EMPTY record (there is no hole to find), so without it a tenant
    whose backfill never ran would read as ready and be served an empty dense
    leg as though it were a small corpus.

    ⛔ BOUNDED BY THE READ'S BUDGET, AND ONLY THE STATEMENT IT NEEDS (#13331
    review F3). This runs on the REQUEST's thread, before :func:`searcher`
    returns, so an unbounded door here is the `#11691` defect one function above
    the fix for it: the door's ceiling is ``RECORD_STATEMENT_TIMEOUT_MS`` (15 s)
    and the read's own budget is typically 500 ms, so a wedged record could
    stall the request for the door's ceiling *before* the leg's budget existed
    at all. Two things keep that shut, and both are needed:

    * the budget is DIVIDED by :func:`record_statement_ms` — the same one
      convention :func:`vector_search` uses — and reaches the door as its
      ``statement_timeout_ms``, so the read's budget TIGHTENS the ceiling and
      never widens it;
    * ``want_gap=False``: :func:`journal_coverage` otherwise issues a SECOND
      statement (``_FIRST_GAP_SQL``) whose answer this function never reads —
      readiness asks only ``unprojected`` and ``present``. Issuing it would
      spend the read's budget on a fact nobody consumes.

    ⛔ AND THE JOURNAL IS COUNTED READ-ONLY (#13331 review F2).
    `EventLog.position_readonly()` is `position()` without its side effects: no
    append lock (so a wedged peer cannot park this call, and a timeout here can
    never stamp the path `_FLOCK_UNHEALTHY` and suppress a concurrent writer's
    `seq` for 30 s), no ``a+b`` open (so a journal mounted READ-ONLY is
    countable instead of an endless `OSError`), and ``None`` for a path it could
    not count — which is this function's ``False``, not the ``0`` that would read
    as "the journal is empty, so the record holds all of it" (review F3/B).
    """
    path = os.environ.get(RECORD_JOURNAL_ENV)
    try:
        from .log import EventLog

        enumerated = EventLog(path).position_readonly()
    except Exception as exc:
        logging.getLogger(LOG_NAME).debug(
            "record dense leg: the declared journal %r could not be counted "
            "(%s) — keeping the graph branch", path, type(exc).__name__)
        return False
    if enumerated is None:
        # ⛔ NOT ZERO (#13331 review F3/B). A declared path that does not exist
        # is the shape a mistyped value or an unmounted volume takes, and a `0`
        # here makes `unprojected` 0 for any tenant whose record holds a row —
        # i.e. the gate reports a journal it never counted as fully projected.
        # WARNED once per path, not DEBUG: see `_warn_readiness_unmeasurable`.
        _warn_readiness_unmeasurable(path)
        return False
    conn = None
    try:
        from .record_projection import journal_coverage
        from .record_serve import open_record

        # ⚠️ TWO CALL SHAPES ON PURPOSE, as in `vector_search`: with no budget
        # the door is asked exactly as it was before this fix (no kwarg, the
        # door's own bound), so the unbudgeted callers and the one-argument
        # doubles that stub this door stay honest about what is asked for.
        conn = (open_record(record_dsn) if timeout_ms is None
                else open_record(record_dsn,
                                 statement_timeout_ms=record_statement_ms(timeout_ms)))
        cov = journal_coverage(conn, org_id=scope.org_id,
                               graph_id=scope.graph_id,
                               enumerated=enumerated,
                               want_gap=False)
    except Exception as exc:
        logging.getLogger(LOG_NAME).debug(
            "record dense leg: the coverage check could not answer (%s) — "
            "keeping the graph branch", type(exc).__name__)
        return False
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
    # Exact: no position the journal enumerates may be missing from the record.
    # `present > 0` guards the shape where the journal itself is empty (a brand
    # new tenant whose first live write has not landed): there is nothing to
    # serve, and the graph branch is the correct answer.
    # ⛔ `unprojected` answers `None` — never `0` — when the reading is not a
    # measurement, and the JOURNAL-AHEAD-IMPOSSIBLE shape is one of them: a
    # record holding MORE positions than its journal enumerates cannot be a
    # fold of that journal (it is the empty-journal case when the journal
    # enumerates none, and a rotated/replaced file when it enumerates fewer
    # than the record holds), so `max(enumerated - within_prefix, 0)` there is a
    # detectable contradiction, and `== 0` below would arm the dense leg on the
    # strength of it (JournalCoverage.unprojected_unknown_reason,
    # #13371/#13345 P2/F2). The `None` cannot come
    # from an uncountable journal here — that arm returned above — so it is
    # exactly that contradiction, and it is REPORTED rather than silently off.
    if cov.unprojected is None:
        _warn_readiness_contradictory(path, cov)
        return False
    return cov.unprojected == 0 and cov.present > 0


#: The shape `run_vector_query` consumes for BOTH legs: ``(id, distance,
#: recency)`` rows, or ``None`` for "this label is not served here". The FOURTH
#: positional argument is the caller's `#689` `excluded_statuses` — declared so a
#: reader of the annotation cannot conclude the status filter is unavailable
#: (#11660 review round 6, P3-7).
DenseSearcher = Callable[
    [str, Sequence[float], int, tuple | None],
    list[tuple[str, float, Any]] | None,
]


def searcher(
    record_dsn: str | None = None,
    carried: tuple[str, str] | None = None,
    timeout_ms: int | None = None,
) -> DenseSearcher | None:
    """A dense searcher bound to this request's tenant, or ``None`` when unarmed.

    The returned callable has the signature
    ``search(entity_type, query_vec, limit, excluded_statuses=None) ->
    [(id, distance, recency), …]`` and its shape is deliberately the SHAPE THE
    CYPHER YIELD PRODUCED, so `run_vector_query`'s single ordering pass consumes
    both legs identically and the ranking cannot drift between them by
    construction.

    ⛔ THE FOURTH PARAMETER IS NOT OPTIONAL IN THE CONTRACT (#11660 review round
    6, P3-7): ``excluded_statuses`` is the caller's `#689` terminal-status
    exclusion (`None` = exclude the default set, `()` = the audit opt-in that
    emits no clause). A caller following a three-argument description would not
    know the status filter exists, and the graph branches apply it on every
    Point read — so leaving it out is how the two legs start answering different
    populations again.

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

    ⛔ ``timeout_ms`` IS THE READ'S OWN BUDGET, BOUND HERE AT CONSTRUCTION (#11691)
    — deliberately NOT a fifth argument on the returned callable. The four-argument
    shape above is load-bearing (one ordering pass consumes both legs, so the
    ranking cannot drift by construction), and a per-call budget argument would be
    the same drift re-opened in a new position: a caller that omitted it would
    silently get the door's 15 s bound again.

    Why it has to be bound at all: ``run_vector_query`` gives the GRAPH branches
    500 ms (``graph.query(..., timeout=timeout_ms)``) while this leg inherited
    ``record_serve.RECORD_STATEMENT_TIMEOUT_MS`` = **15 s** plus a 10 s connect
    bound — a 30x mismatch. The fused read then JOINS its workers
    (``ThreadPoolExecutor.__exit__``, ``search_engine.degradation_chain``), so the
    cap does not save the caller: one slow record query turned a 500 ms search
    into a multi-second one and the leg was recorded as ``timeout``, discarding a
    result it may have had. With the budget bound here the statement timeout is
    ``min(RECORD_STATEMENT_TIMEOUT_MS, record_statement_ms(timeout_ms, statements))``
    — the door's ceiling, tightened, and then DIVIDED by the count of statements
    THIS label's arm issues (``statements`` is decided in :func:`vector_search`,
    where the folded/column split is known — not in this closure) — so the query
    is cancelled with part of the read's budget still on the clock, the graph
    branch answers inside that remainder
    (``run_vector_query`` pins it to ``search_engine.remaining_read_ms`` of the
    record leg's own start, #11691 review P2), and the trace records the NAMED
    fallback (``scan_fallback``) instead of a ``timeout`` with no rows.

    ⚠️ ``timeout_ms`` IS THE READ'S BUDGET, NOT THE STATEMENT'S, and the
    difference is not cosmetic: a budget applied whole to each statement would
    let one leg spend the whole read. ⛔ HOW MANY STATEMENTS IS PER-ARM, and the
    divisor is NOT always :data:`RECORD_LEG_STATEMENTS_PER_READ` (`#13466`
    cycle 5, P3): a **column-filtered** label issues two (the kNN and, on the
    short path, the shortfall ``count(*)``) and divides by
    :data:`RECORD_LEG_STATEMENTS_PER_READ`; a **folded-status** label
    (`object`/`subject`) issues three (its two prefetches, then the kNN) and
    divides by :data:`FOLD_ARM_DIVISOR`. Reading only this paragraph is how a
    lane diagnosing a folded ``scan_fallback`` gets sent to the wrong arm's
    arithmetic. The division happens in ONE place
    (:func:`record_statement_ms`), so the arithmetic that keeps the fallback
    inside the deadline has a single home rather than a constant copied into
    every caller.

    ``None`` (the default) leaves the door's own bound in place, unchanged — the
    reconstruction reads (health, replay, backup, restore) legitimately need
    seconds, which is why the ceiling is a per-door constant and this is a
    tightening only. Pinned by
    ``tests/test_11691_vector_leg_budget.py::test_the_record_leg_is_bound_by_the_reads_own_budget``.

    ⚠️ THE ONE WINDOW THIS DOES NOT BOUND, stated rather than implied: the
    CONNECT. ``record_writer._default_connect`` applies
    ``CONNECT_TIMEOUT_SECONDS`` (10 s) — or a ``connect_timeout`` the operator put
    in the DSN — and neither is reachable from here, because the handle is pooled
    (#9558) and its connect parameters are the pool's, not this read's. So the
    STATEMENTS are bounded by a share of the read's budget
    (:func:`record_statement_ms`) and the connect is not. It is named
    rather than fixed because bounding it means changing what the POOL hands
    every other borrower; the measured failure this issue prices (a 15 s
    STATEMENT on a 500 ms read) is the one that was observed.
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
    if not cutover_armed():
        # See ARMING_ENV: the record is a fold with no backfill, so it does not
        # yet hold the whole corpus and must not become the dense leg's only
        # source. Unarmed is the DEFAULT and it is not a degraded state.
        return None
    scope = _resolve_scope(carried)
    if scope is None:
        return None
    # #13331 requirement 5 — the PER-TENANT condition on the deployment switch:
    # a tenant whose record does not hold its journal is not served from it. This
    # is the arm `#13210` §7.4 said could only start moving once live writes were
    # wired: a fresh tenant's positions are consumed at write time, so its record
    # is authoritative for new rows with no backfill to wait for.
    if not journal_ready(scope, record_dsn=record_dsn,
                         timeout_ms=timeout_ms):
        logging.getLogger(LOG_NAME).debug(
            "record dense leg: tenant %s/%s's record does not hold its journal "
            "yet (or could not be measured) — the graph branch answers, which "
            "is the correct branch and not a degraded one (#11642 P1, #13331)",
            scope.org_id, scope.graph_id)
        return None

    def _search(
        entity_type: str, query_vec: Sequence[float], limit: int,
        excluded_statuses: tuple | None = None,
    ) -> list[tuple[str, float, Any]] | None:
        return vector_search(scope, entity_type, query_vec, limit,
                             record_dsn=record_dsn,
                             excluded_statuses=excluded_statuses,
                             # The read's budget, captured HERE (on the request's
                             # thread, at construction) rather than passed per
                             # call — see this function's docstring (#11691).
                             timeout_ms=timeout_ms)

    return _search


def vector_search(
    scope: _Scope,
    entity_type: str,
    query_vec: Sequence[float],
    limit: int,
    *,
    record_dsn: str | None = None,
    excluded_statuses: tuple | None = None,
    timeout_ms: int | None = None,
) -> list[tuple[str, float, Any]] | None:
    """The kNN read — bounded per STATEMENT, tenant-scoped, shortfall asserted.

    THREE outcomes, and the third is the one that is easy to get wrong:

    * ``None`` — **no record table serves this ``entity_type`` at all**. The caller
      MUST take its graph branch; this is not an empty answer and must never be
      rendered as one.
    * ``[]`` — served, asked, and the filter genuinely selects nothing.
    * rows — the answer.

    Raises :class:`RecordVectorDeclined` when a SERVED label declines THIS read (its
    folded terminal-key set exceeded :data:`_FOLDED_TERMINAL_KEY_BUDGET`) — a
    different answer from ``None`` on purpose, because the caller must NAME the
    fallback and hand the graph only the remainder of the read;
    :class:`RecordVectorError` when the record cannot answer (same two consequences);
    and :class:`VectorShortfall` when it answered with fewer rows than the filter
    selects (so the caller reports an incomplete answer rather than a small one).

    ``timeout_ms`` is the READ's budget (#11691) — not the statement's. It is
    divided once (:func:`record_statement_ms`) and the quotient is what
    :func:`tortoise.record_serve.open_record` applies, as
    ``min(RECORD_STATEMENT_TIMEOUT_MS, quotient)``: the door's ceiling, tightened,
    never widened. ``None`` keeps the door bound. A query cancelled by it raises
    (``QueryCanceled``) and surfaces as :class:`RecordVectorError`, i.e. the
    caller's NAMED graph fallback rather than a silent multi-second stall: without
    the bound a slow record read ran to 15 s while the read's own cap was 500 ms,
    and the leg was then recorded as ``timeout`` with its rows discarded.

    ⛔ THREE STATEMENTS AT MOST, NAMED RATHER THAN IMPLIED — and WHICH THREE
    depends on the label. ``statement_timeout`` bounds each statement, so the
    read's budget is divided into shares instead of being applied whole, and the
    DIVISOR is one more than the arm issues (:func:`record_statement_ms`), so the
    share the arm does not spend is what the graph branch answers from after a
    cancellation — bounded there by ``search_engine.remaining_read_ms``, so the
    reserve is spent by the graph branch rather than merely left unclaimed
    (#11691 review P2).

    * a **column-filtered** label (`point`, `operator`, `source`, `document`,
      `event`) issues the kNN read and — on the SHORT path only — the shortfall
      ``count(*)``: at most two statements, so it divides by
      :data:`RECORD_LEG_STATEMENTS_PER_READ` (three) and the graph keeps the
      remaining THIRD;
    * a **folded-status** label (`object`, `subject`, `#13422`) issues the fold's
      prefetch (its rows, then the tenant's status-bearing journal events) and
      then the kNN read: three statements, so it divides by
      :data:`FOLD_ARM_DIVISOR` (four) and the graph keeps the remaining QUARTER.
      It spends NOTHING on a ``count(*)``
      because the prefetch already returns the exact `available` figure, which is
      why the cage does not push it to four.

    ⚠️ That reserve is not slack, and how MUCH of it exists depends on the arm: a
    column-filtered label keeps a third, a folded one keeps a quarter — it does
    NOT have none left (dividing by
    :data:`RECORD_LEG_STATEMENTS_PER_READ` for a three-statement arm is exactly
    the bug `#13466` cycle 4 fixed, since a floored ``1 ms`` is no reserve at
    all). A record
    read that overruns its share is CANCELLED and falls through to the graph
    branch as the named ``scan_fallback``, which is correct-but-slower rather
    than wrong; the cost is real and it is named here so a lane reading a
    `scan_fallback` trace for these two labels knows which prefetch to look at
    before concluding the record is broken — and can size the bound from the
    arm's OWN divisor (budget / 4 here, budget / 3 for the column arm) rather
    than from a single figure that is wrong for one of them. Reducing the leg to
    fewer statements
    is not available — the shortfall assertion is what stops a truncated kNN
    answer being rendered as a small corpus (§6.1) — so it is bounded by
    arithmetic rather than removed.
    """
    mapping = LABEL_SERVED.get(entity_type)
    if mapping is None:
        # A label no record table serves. NOT an empty result: the caller's graph
        # branch is this label's real path and always was.
        return None
    table, id_column, kind_filter, recency_columns = mapping
    if limit <= 0:
        return []
    vec = [float(x) for x in query_vec]
    if not vec:
        return []

    from .record_projection import scope_connection
    from .record_serve import open_record

    literal = "[" + ",".join(repr(v) for v in vec) + "]"
    # ⛔ THE DIVISOR IS DECIDED HERE, BEFORE ANY STATEMENT RUNS, because the DOOR
    # applies it: a folded label issues :data:`FOLD_ARM_STATEMENTS` data statements
    # (its two prefetches, then the kNN) against the column arm's
    # :data:`RECORD_LEG_STATEMENTS_PER_READ` divisor, so it must divide by
    # :data:`FOLD_ARM_DIVISOR` — one MORE than it issues — or it spends the whole read
    # and hands the graph branch a floored 1 ms. See `record_statement_ms`.
    status_source = _STATUS_COLUMN.get(entity_type)
    folded = status_source is not None and status_source[0] == _FOLDED_STATUS
    statements = (FOLD_ARM_DIVISOR if folded
                  else RECORD_LEG_STATEMENTS_PER_READ)

    try:
        # ⚠️ TWO CALL SHAPES ON PURPOSE: with no budget the door is asked exactly
        # as it was before #11691 — no kwarg, the door's own bound — so the
        # unbudgeted path is byte-identical (and the pins that stub `open_record`
        # with a one-argument double stay honest about what the leg asks for).
        # A budget is the READ's, so it is divided here and only the quotient
        # reaches the door — see `record_statement_ms` for why (the divisor is
        # the ARM's, and it counts one share to leave behind for the graph
        # branch, so a folded label divides by four and a column-filtered one by
        # three; not a flat "two statements").
        conn = (open_record(record_dsn) if timeout_ms is None
                else open_record(record_dsn,
                                 statement_timeout_ms=record_statement_ms(
                                     timeout_ms, statements)))
    except Exception as e:  # noqa: BLE001, RUF100 — every failure is "the record did not answer"
        raise RecordVectorError(
            f"the record could not be opened ({type(e).__name__}) — the dense "
            "leg did not answer") from e
    try:
        cur = scope_connection(conn, scope.org_id)
        try:
            # ── THE PREDICATE, built HERE because one arm needs the record ──
            # A label whose status is a COLUMN filters in SQL. A label whose
            # status is FOLDED (object/subject) cannot: the value is a
            # projection-owned cache the schema refuses as a column, so the leg
            # asks the record's own fold which of this tenant's keys are terminal
            # and excludes them by key. Building the statement after that
            # decision — rather than before it — is what keeps the two arms from
            # drifting into one statement that silently applies neither.
            where = ["t.embedding_vec IS NOT NULL"]
            key_params: list[Any] = []
            available: int | None = None
            if kind_filter:
                where.append(kind_filter)
            if folded:
                fold = _folded_terminal_keys(
                    conn, cur, scope, entity_type, excluded_statuses)
                if fold is None:
                    # ⛔ THE PREFETCH ITSELF IS OVER BUDGET (rows, or journal
                    # events) — see _FOLDED_PREFETCH_ROW_BUDGET. It DECLINES the
                    # same way the bind budget below does, and for the same two
                    # reasons: the caller must NAME the fallback (`#4999`) rather
                    # than take a silent `scan`, and it gets only the remainder of
                    # the read (`#11691`). A TRUNCATED fold is not an option — the
                    # terminal keys it dropped are rows this leg would SERVE.
                    # ⚠️ `info`, matching the record-leg fallbacks in
                    # `search_engine.run_vector_query` (`info`/`debug` for the same
                    # event). A tenant permanently past the budget would otherwise
                    # emit one WARNING per `object`/`subject` dense read, for ever —
                    # a bounded decline is a designed answer, not an incident.
                    logging.getLogger(LOG_NAME).info(
                        "the record's `%s` fold prefetch is over its row/event "
                        "budget, so this read declines to the graph branch (a "
                        "bound, not a degradation)", entity_type)
                    raise RecordVectorDeclined(
                        f"the `{entity_type}` fold prefetch is over its "
                        f"row/event budget ({_FOLDED_PREFETCH_ROW_BUDGET} rows, "
                        f"{_FOLDED_PREFETCH_EVENT_BUDGET} events)")
                terminal_keys, available = fold
                if len(terminal_keys) > _FOLDED_TERMINAL_KEY_BUDGET:
                    # See _FOLDED_TERMINAL_KEY_BUDGET. RAISED, not `return None`:
                    # the leg has already spent its prefetch, so the caller must
                    # NAME the fallback (#4999) and hand the graph only the
                    # remainder of the read (#11691). See
                    # `RecordVectorDeclined`.
                    logging.getLogger(LOG_NAME).info(
                        "the record's fold calls %d `%s` keys terminal — over "
                        "the %d-key bind budget, so this read declines to the "
                        "graph branch (a bound, not a degradation)",
                        len(terminal_keys), entity_type,
                        _FOLDED_TERMINAL_KEY_BUDGET)
                    raise RecordVectorDeclined(
                        f"the folded terminal-key set for `{entity_type}` holds "
                        f"{len(terminal_keys)} keys, over the "
                        f"{_FOLDED_TERMINAL_KEY_BUDGET}-key bind budget")
                if terminal_keys:
                    # The LEG's key column, not the record's `node_key` — see
                    # `_folded_terminal_keys`'s note on why the two differ.
                    where.append(f"t.{id_column} <> ALL(%s)")
                    key_params.append(terminal_keys)
            else:
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
            params = (literal, scope.org_id, scope.graph_id, *key_params,
                      literal, int(limit))
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
            # apart, so on the SHORT path only we learn how much material the
            # filter selects. It is not free and it is not silent — a caller that
            # wants it off must say so by arming nothing.
            #
            # ⛔ TWO WAYS TO LEARN IT, and the folded arm takes the free one: a
            # column-filtered label pays one bounded aggregate, while a folded
            # label already holds the exact number from its prefetch (`available`)
            # and must NOT spend a fourth statement re-asking it. Using the
            # `count(*)` there instead would blow the read's statement share and
            # turn an answerable read into a cancellation.
            if len(rows) < int(limit):
                if available is None:
                    cur.execute(
                        f"SELECT count(*) FROM public.{table} AS t "
                        f"WHERE t.org_id = %s AND t.graph_id = %s AND {predicate}",
                        (scope.org_id, scope.graph_id, *key_params),
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
    except (VectorShortfall, RecordVectorDeclined):
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
