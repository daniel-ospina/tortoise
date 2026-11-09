"""C4 (#2517 / #2568, #2513): source-session re-injection on seeded hits.

The reader-surface + pool-rank-cut lever for the multi-session partial-
evidence bucket (docs/scoping/2026-09-07-2513-multisession-evidence-
surface.md §4 C4): a seeded hit's own source session holds more verbatim
material than the pool already carries, and that material can be
question-relevant but rank below the reader window, so the verbatim
evidence never reaches the reader (and, for the all-or-nothing
``recall_all@5`` binary, a pool-present starved session sits at
rank >= 6). This module ships the product rules as pure primitives plus
ONE bounded graph pass:

1. **SEED** — :func:`seeded_sessions`: the distinct REAL ``session_id``
   values represented in the reader-reachable pool head, in first-seen
   rank order, bounded by ``limit``. The caller passes the reader's OWN
   admission predicate (``admitted=retrieval._has_claim_text``), so a hit
   the reader would skip for #2978 consumes no ``window`` slot either:
   the seed tracks the reader's item slots rather than a raw prefix
   (#3594). It is a TWO-SIDED, not one-sided, approximation — the
   reader's budget caps are unmodelled, so a cap-dropped hit still
   consumes a seed slot and can hide a later reader-admitted session.
   Label-free: the trigger is RANK, never
   a stored/read-time mark (the mark-triggered variant was rejected as
   gold leakage — the product has no such mark; #2513 §1.1). The
   synthetic ``idx:N`` bucket key is dropped (it can never equal a graph
   session identity, so seeding it would be a phantom session). Each seed
   also carries the POOL HIT ID that seeded it — the fetch anchors on
   that hit (see EXPAND).
2. **EXPAND** — :func:`source_session_chunk_pass`: ONE batched Cypher
   fetch per FIRED question (never one per seed), with the per-session and
   total caps applied in Python to the returned rows (the query carries no
   ``LIMIT``; ``WITH DISTINCT s`` anchors the traversal on the seeded
   sessions so the driver scan is the seed list, not the whole ``Point``
   label). The pool-membership filter lives IN the query
   (``NOT p.id IN $pool_ids``) so the budgets are spent on genuinely-new
   items. Fail-open: any failure yields the empty result and the caller
   keeps the ORIGINAL pool.

   **What the fetch targets (product-real by construction).** The default
   ``chunk_kind`` is :data:`TURN_POINT_KIND` — the episodic TURN points the
   product actually writes (``hosted POST /v1/sessions``; the SDK
   ``capture_session`` writer is now the private `TortoiseSDK._capture_session`, after
   #4282 retired the public name) — NOT ``session-transcript``, which is
   written ONLY by the eval ingest lane and therefore made the fetch
   unreachable in any product graph.

   **What scopes it (no benchmark-only field).** The fetch matches
   ``(s:Session)-[:CONTAINS]->(p:Point)`` for the Session that CONTAINS a
   seeded pool hit, and groups by ``coalesce(p.session_id, s.id)``. This is
   the product's OWN session membership: a product turn Point carries NO
   ``session_id`` property at all (the capture loop writes content/kind/
   speaker/is_episodic and links ``Session-[:CONTAINS]->Point``), so any
   ``p.session_id IN $sids`` predicate is dead in the product; and the
   previous ``p.lme_question_id = $q`` guard was a benchmark-only property
   no product writer emits.

   Question scope comes from TWO independent things, and neither is the
   removed guard: (1) the eval ingests each question into its OWN graph
   namespace with a per-question wipe, so no graph ever holds two
   questions' points — the corpus-level ``session_id`` collisions (212
   shared dataset session ids across the 100-question tail cohort) never
   co-exist in one graph; (2) the anchor itself — the seeded hit is THIS
   question's pool hit, so the Session it belongs to is this question's
   Session.

   ⚠️ Residual, NOT a guarantee: a seed that is a CONTENT-ADDRESSED point
   shared across sessions is linked into EVERY Session that folds it
   (``ingest_v2._apply_noops`` for the eval; the product's dedup-by-
   content-hash hit for ``capture_session``), so
   ``(s:Session)-[:CONTAINS]->(seed)`` can resolve more than one Session
   and the fetch expands all of them. ``WITH DISTINCT s`` dedups that
   traversal; it does not constrain the fetch to one Session per seed.
   Within the eval this stays question-scoped (one graph per question) and
   within the product every containing Session is a legitimate source
   session, but a seed shared across sessions widens the fetch.

   ✅ SEED identity AT THE IDENTITY LAYER — closed by #10540. The fetch
   TARGET was already product-real; the SEED gate
   was blind, because the pool key authority
   (``retrieval.session_key_of``) read the snake ``session_id`` prop ONLY and
   the product's point fetch POPS that prop from EVERY point hit it returns
   (turn and extracted alike — ``sdk.tortoise_fts_query`` builds the entry,
   then ``entry.pop("session_id")``), leaving the camel ``sessionId`` as the
   hit's only identity. So a product pool hit keyed as the per-hit phantom
   ``idx:point:{id}`` and :func:`seeded_sessions` dropped it: on the raw
   search pool the arm could seed nothing at all, and an ASK-lane pool only
   where ``annotate_ask_hits`` had re-attached a snake id — from the
   ``:Event``'s ``sessionId`` or from the Point's own prop, since it sets
   ``"session_id": ev_session or n_session`` (that lane dedups with its own
   ``ask_session_key``, and the arm is off in the product).

   ``session_key_of`` now also reads the CAMEL wire identity ``sessionId``,
   which is the spelling the product's own point fetch emits: it resolves the
   session from ``n.sessionId`` / the snake prop / the ``:Session CONTAINS``
   edge and then pops the snake key (``sdk.tortoise_fts_query``), i.e. the
   reader ALREADY saw the identity (``retrieval.hit_session_id`` → ``[session
   A]``) while the pool one layer below it did not. So a turn hit CAN seed its
   own session, and :func:`reinjection_merge_order` anchors that session's
   injected group after its own base ranks.

   ⛔ SCOPE of the closure in this section — it is the IDENTITY, not the ARM.
   Three facts a reader must not lose: (1) **no caller under ``tortoise/``
   invokes this module** — every call site is the eval driver
   (``tools/longmem_eval/retrieve.py``) — so the arm is still OFF in the
   product, exactly as the ASK-lane sentence above says; (2) every pool
   the arm consumes today is built by ``_annotate_hits``, which stamps
   ``session_id`` AND ``lme_session_index`` on EVERY hit
   (``tools/longmem_eval/retrieve.py``), both of which outrank the camel leg —
   and it COERCES an absent index into an explicit ``-1``
   (``p.get("lme_session_index", -1)``), which keys ``idx:-1`` and outranks the
   camel leg the same way — so on today's only live path the camel branch is
   not reached at all; and (3) an id OUTSIDE the render allowlist is emitted
   by the fetch as ``sessionId: ""`` (``_safe_session_tag``, applied at the
   producer), so the reader calls it UNKNOWN and the pool calls it a phantom —
   they AGREE, and such a session (a caller-supplied ``"session/2026-11-05
   10:00"``) is still unseedable. What #10540 changes is what the AUTHORITY
   answers for a product-shaped hit; it does not wire the arm, it does not
   widen the allowlist, and it moves no eval-visible key. (The defensive tail
   is NOT the pre-fix symptom and not a fallback either: a group only exists
   for a key in ``seed_order``, whose own pool hit always supplies an anchor
   rank, so an absent group key is DROPPED by the merge rather than tailed —
   see :func:`source_session_chunk_pass`; pinned by
   ``tests/test_session_reinjection_rules.py::test_an_unseeded_group_key_is_``
   ``dropped_never_tailed``.) The GRAPH side is unchanged and that is
   deliberate: the fetch still scopes by ``(s:Session)-[:CONTAINS]->(p:Point)``
   and still RETURNs ``coalesce(p.session_id, s.id)``, because the Point
   property is what a *turn Point* does not have — only the PASSAGE-side hit
   identity was missing.

   **Turn-shape constraint.** ``pointKind``'s vocabulary is open
   (``create_point`` accepts any registered kind), so ``pointKind='event'``
   alone does not prove a TURN: a graph seeded before #10000 by the hosted
   demo/dashboard seed holds ``pointKind='event'`` points with NO
   ``is_episodic`` (``hosted_api._seed_demo_graph``; since #10000 it writes no
   turn node at all) — their body IS ``[role]``-tagged, so the ``is_episodic``
   conjunct is what excludes them — and any SDK/API caller can mint one.
   ``_TURN_SHAPE_FILTER`` therefore requires the shape EVERY product and
   eval turn writer emits and no non-turn ``event`` writer does:
   ``is_episodic=true`` plus a ``[...]``-prefixed body.
3. **MERGE** — :func:`reinjection_merge_order`: purely ADDITIVE. Each
   seeded session's injected group is spliced immediately AFTER that
   session's LAST base rank in the pool (never at the head — a
   competitive head placement cannot raise a distinct-session set metric
   and can only evict another session's evidence), in seed-rank order,
   dropping already-present ids. Then the shared
   ``retrieval.guard_and_recap_pool`` (optional session-diverse window
   guard + the C5 per-session raw-chunk re-cap, one contract call).

C5 posture: the re-cap is respected, never overridden — an injected chunk
can never evict a base chunk of its own session (it is anchored after the
session's last base hit), and a session already at the C5 chunk ceiling
legitimately receives nothing (the reach boundary is readable in the
census, not hidden).

⚠️ **At the shipped turn grain the C5 re-cap does NOT bound the
injection.** ``dedup_pool`` counts only ``is_raw_chunk`` hits against
``max_chunks_per_session`` (D3 #1540: turn points are the compact
epistemic surface, never chunk-capped), so injected TURN points pass the
re-cap untouched. The total budget is therefore the ONLY volume guard on
the default path — which is why
:data:`DEFAULT_REINJECTION_TOTAL_ITEMS` was retuned to a value that
actually binds (see that constant) rather than left as unreachable
headroom.

Coupling: ``retrieval → coverage_loop`` is the pinned one-way direction;
this module imports the shared contract from ``tortoise.retrieval`` only
(the ``DEFAULT_POOL_*`` aliases resolve there) — it adds NO new module
edge. OFF by default; the eval arms it via
``--session-reinjection`` / ``TORTOISE_LME_SESSION_REINJECTION``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from tortoise.retrieval import (
    DEFAULT_POOL_GUARD_WINDOW,
    DEFAULT_POOL_SESSION_CAP,
    TURN_POINT_KIND,
    guard_and_recap_pool,
    session_key_of,
)

logger = logging.getLogger(__name__)

#: The default seed window: how many reader-ADMITTED hits the seed walks.
#: The eval derives the effective window from the resolved reader item cap
#: (``eff_item_cap``) so a non-default ``TORTOISE_LME_CONTEXT_ITEMS`` cannot
#: silently desynchronise it; this constant is the product fallback.
#:
#: NOT exact: ``retrieval.assemble_context`` SKIPS claim-text-less hits
#: (#2978, decoration-only nodes such as operators) without consuming an
#: item slot, so the reader may admit hits BELOW rank ``window``. #3594
#: closed that gap: the driver passes the reader's own admission predicate
#: (``admitted=retrieval._has_claim_text``), so ``window`` counts ADMITTED
#: hits and this value is the fallback for when the reader item cap is
#: unresolved. What remains is the reader's BUDGET CAPS, which are
#: budget-dependent and unmodelled — a cap-dropped hit still consumes a
#: seed slot, so the seed can also MISS a reader-admitted session below it.
#: That is a TWO-SIDED approximation, not a one-sided bound.
DEFAULT_REINJECTION_SEED_WINDOW = 40

#: Distinct seeded sessions per fired question (bounded fan-out).
DEFAULT_REINJECTION_SEED_SESSIONS = 5

#: Injected items per seeded session (the per-session budget).
#:
#: Unchanged by the turn-grain retarget (#2517). A turn is the FINER unit,
#: so three turn points carry no more verbatim material than the three
#: chunk windows they replace; and unlike injected chunks they are NOT
#: clawed back by the C5 re-cap (``dedup_pool`` counts only
#: ``is_raw_chunk`` hits), so the conservative choice at the finer grain is
#: to widen nothing here — the volume guard on the default path is the
#: TOTAL budget below.
DEFAULT_REINJECTION_PER_SESSION = 3

#: Injected items per fired question, across all seeds (the total budget).
#:
#: ⚠️ Reachability condition: ``total_cap <= SEED_SESSIONS *
#: PER_SESSION`` (= 15). Above 15 the total is structurally inert — the
#: per-session cap alone bounds RETAINED items at 15, so no 16th item is
#: ever admitted and ``source_session_chunk_pass``'s ``total_cap_hit``
#: could never be True. (The budget is charged per DISTINCT admitted item,
#: and the total check runs BEFORE the per-session check, so a 16th
#: CANDIDATE row can still trip the key at ``total_cap == 15``; the shipped
#: value is the conservative choice strictly below that line, not at it.)
#: At the shipped ``10`` the total IS reachable (the 11th genuinely-new
#: item trips it), so ``total_cap_hit`` is a live census signal and
#: ``dropped_by_cap`` counts BOTH kinds of drop. The invariant is pinned by
#: ``tests/test_session_reinjection_rules.py::
#: test_total_budget_is_below_the_structural_fan_out`` so a future change
#: to either constant cannot silently re-inert the key.
#:
#: 10 (not 15) is the retuned value: at turn grain the fetch's candidate
#: pool per session is the session's whole turn list (order ~10^1) rather
#: than its handful of chunk windows, so the total budget — not the
#: per-session cap — is now the binding guard on injection volume.
DEFAULT_REINJECTION_TOTAL_ITEMS = 10

#: The TURN SHAPE constraint the fetch applies on top of ``pointKind``
#: (#2517 review). ``pointKind``'s vocabulary is OPEN — ``create_point``
#: accepts any registered kind — so ``pointKind='event'`` alone does not
#: prove a node is a turn. Evidence:
#:
#:   * every TURN writer (``TortoiseSDK.capture_session`` `sdk.py:~3351`, whose
#:     public name #4282 retired — the body survives as `_capture_session`;
#:     the hosted turn loop `hosted_api.py:~8227`, and the eval legs
#:     `ingest.py:~207` / `ingest_v2.py:~543`) writes ``is_episodic=true``
#:     AND a body of the deterministic form ``f"[{role}] {content}"`` — so
#:     the body always STARTS WITH ``[``;
#:   * ⚠️ the hosted demo/dashboard seed (`hosted_api.py` `_seed_demo_graph`)
#:     USED to be the NON-turn ``pointKind='event'`` writer here — it wrote no
#:     ``is_episodic``, and its body was ``[role]``-tagged, so the predicate
#:     excluded it on the ``is_episodic`` conjunct alone. Since #10000 it
#:     writes no turn node at all (§9.1/D1), so that clause now describes no
#:     writer in the tree; it is kept because a graph seeded BEFORE #10000
#:     still holds those nodes and they must stay excluded;
#:   * so this predicate excludes it and any caller-minted bare ``event``
#:     Point, while holding for every legitimate turn.
#:
#: The check is deliberately NOT ``speaker IS NOT NULL``: the ``speaker``
#: property arrived with delta 5 (#721) and set it alongside, so requiring
#: it would silently exclude legacy turns for no additional exclusion power
#: (the demo seed's exclusion is already carried by ``is_episodic``).
_TURN_SHAPE_FILTER = ("coalesce(p.is_episodic, false) = true "
                      "AND coalesce(p.content, '') STARTS WITH '['")

#: ⚠️ #10000 RESIDUAL, stated where the predicate lives rather than left to be
#: discovered: the turns this fetch expands to are TURN NODES, and §9.1/D1 moved
#: the raw transcript OUT of the graph once the record is armed — after an armed
#: capture the graph holds the Session and its counters, and no `:Point` for the
#: turn node for the session (measured: `sdk._capture_turn_ids` -> `[]`). So on an
#: armed deployment this fetch has nothing to expand to, and C4 reinjection is a
#: silent no-op for exactly the sessions it exists to enrich.
#:
#: It is NOT re-pointed here because the seeds are the retrieval POOL's hits —
#: graph search results — so "the transcript is reachable from the pool" is a
#: retrieval question (§9.1 puts the text in Postgres and the graph indexes what
#: it extracts), not a call-site swap. Deciding how retrieval sees transcripts is
#: its own change; recorded on #10000 with the same evidence as this note, so the
#: gap is owned instead of rediscovered.


@dataclass(frozen=True)
class SeededSession:
    """One seeded session: its ``session_id``, the POOL HIT ID that seeded
    it, and that hit's rank.

    ``point_id`` is what the fetch anchors on: the Session that CONTAINS
    this hit is the session to expand (the product's own membership edge —
    a product turn Point carries no ``session_id``, so the session string
    alone cannot scope the query).
    """
    session_id: str
    rank: int
    point_id: str


def _is_real_session_id(key: str) -> bool:
    """A seed must be a REAL session identity, because the key it yields is
    the merge's splice anchor: ``reinjection_merge_order`` splices an
    injected group only for a key present in ``seed_order`` (the seeds'
    own ``session_id``), so an ``idx:N`` bucket key — which names no graph
    session and can never equal that anchor — would spend the fetch budget
    on a group the merge then DROPS (see ``source_session_chunk_pass``'s
    group-key clause: groups are built over ``seed_order`` alone)."""
    return bool(key) and not key.startswith("idx:")


def seeded_sessions(pool: list[dict], *,
                    window: int = DEFAULT_REINJECTION_SEED_WINDOW,
                    limit: int = DEFAULT_REINJECTION_SEED_SESSIONS,
                    session_key: Callable[[dict], str] | None = None,
                    admitted: Callable[[dict], bool] | None = None,
                    ) -> list[SeededSession]:
    """SEED (pure, label-free): the distinct real sessions represented in
    the reader-reachable pool head, in first-seen rank order, at most
    ``limit``.

    Deterministic and label-free — the only signal is the pool rank the
    retrieval engine already produced. ``admitted`` is the reader's own
    admission predicate: a hit failing it does not consume a ``window``
    slot, so ``window`` counts ADMITTED hits, exactly as
    :func:`retrieval.assemble_context` spends item slots. Callers pass
    ``retrieval._has_claim_text`` (#2978/#3594); with ``admitted=None``
    every hit counts and this reduces to the raw ``pool[:window]`` prefix
    for backward compatibility.

    Why the predicate matters: ``assemble_context`` SKIPS claim-text-less
    hits without spending an item slot, so it admits hits BELOW rank
    ``window``. Seeding from the raw prefix therefore never seeds a
    session whose first pool appearance lands in a skipped-hit gap —
    reader-reachable yet unseeded — which biases the A/B on exactly the
    signal the arm measures. The predicate closes that gap while pool
    ranks stay pool ranks.

    Residual, and it is TWO-SIDED — NOT an upper bound: the token/byte caps
    are budget-dependent and are not modelled here, and ``assemble_context``
    drops a cap-failing hit WITHOUT spending an item slot while this loop
    still counts that hit against ``window``. So the seed can also MISS a
    session the reader admits BELOW a cap-dropped hit — the same
    unseeded-but-reader-reachable bias this predicate exists to remove,
    on the rarer cap path. Read "matches the reader" here as "matches the
    reader MODULO the budget caps". Seeding from ``assemble_context``'s
    returned list would be exact, but the seed runs BEFORE the reader
    assembles (and the reader assembles ``context_candidates``, not
    ``pool``), so that is a pipeline-order change, not a local one (#3594).
    ``limit`` bounds the fan-out.

    A hit that carries no ``id`` cannot be seeded: the fetch anchors on the
    seeded hit to resolve its Session (``Session-[:CONTAINS]->hit``), so a
    seed without one would spend the budget on a session the query cannot
    name. Pool hits always carry their id.
    """
    if not pool or window < 1 or limit < 1:
        return []
    key = session_key if session_key is not None else session_key_of
    out: list[SeededSession] = []
    seen: set[str] = set()
    admitted_seen = 0
    for rank, hit in enumerate(pool):
        if admitted_seen >= window:
            break
        if admitted is not None and not admitted(hit):
            continue
        admitted_seen += 1
        sid = key(hit)
        pid = str(hit.get("id") or "")
        if not pid or not _is_real_session_id(sid) or sid in seen:
            continue
        seen.add(sid)
        out.append(SeededSession(session_id=sid, rank=rank, point_id=pid))
        if len(out) >= limit:
            break
    return out


def source_session_chunk_pass(
        proj: Any, seed_point_ids: list[str], *,
        pool_ids: Any,
        chunk_kind: str = TURN_POINT_KIND,
        per_session_cap: int = DEFAULT_REINJECTION_PER_SESSION,
        total_cap: int = DEFAULT_REINJECTION_TOTAL_ITEMS,
        ) -> dict[str, Any]:
    """EXPAND (one bounded graph pass per fired question, fail-open).

    ``seed_point_ids`` are the pool hit ids that seeded each session
    (:attr:`SeededSession.point_id`). The fetch resolves, for each of them,
    the ``:Session`` that CONTAINS it, then fetches that session's
    ``chunk_kind`` points which are NOT already in the pool — in a
    deterministic ``(session key, lme_chunk_index, id)`` order — and applies
    the per-session and total budgets IN that order.

    Scope, product-real and question-safe:

      * The session is named by the ``Session-[:CONTAINS]->Point`` edge the
        product writes — NOT by a ``p.session_id`` property, which a
        product TURN point does not have, and not by ``lme_question_id``,
        which no product writer emits.
      * Question scope comes from the per-question graph namespace (the
        eval ingests one question per graph, with its own wipe — no graph
        holds two questions' points) AND from the anchor (the seeded hit is
        THIS question's pool hit). The removed ``lme_question_id`` guard
        was redundant under that isolation; it was never what made the
        fetch question-safe.
      * ⚠️ A seed point shared across sessions (content-addressed
        extraction dedup folds one point id into several Sessions) expands
        EVERY containing Session: ``WITH DISTINCT s`` dedups that
        traversal, it does not constrain the fetch to one Session per seed.
      * The group key returned is ``coalesce(p.session_id, s.id)``: the
        eval's dataset session id when the point carries one (it always
        does), the ``:Session`` id in the product (a product turn carries
        no ``session_id``). ⚠️ A key ABSENT from the caller's ``seed_order``
        (reachable through a shared seed — one Point contained by several
        ``:Session`` nodes — or a Point whose own camel ``sessionId`` prop
        names a different session than the one containing it) is **DROPPED**,
        not tailed: :func:`reinjection_merge_order` builds its groups by
        iterating ``seed_order``, so such rows are fetched, charged to the
        per-session/total budget, and then never spliced. The only signal is
        ``total`` (which counts them) against what actually reached the
        merged pool. That function's defensive tail keeps a SEEDED session
        with no base rank; it does not rescue an unseeded group key.

    Default ``chunk_kind`` is :data:`~tortoise.retrieval.TURN_POINT_KIND`,
    the product's verbatim material; passing
    :data:`~tortoise.retrieval.SESSION_TRANSCRIPT_KIND` points it at the
    eval's raw chunk windows instead (the A/B). Whatever the kind, the
    result must ALSO satisfy :data:`_TURN_SHAPE_FILTER` when the kind is
    the turn kind — see that constant for the evidence.

    Returns a report dict (never raises):
      ``ok``            — the query ran (False on any failure).
      ``by_session``    — {session key: [{id, session_id,
                          lme_chunk_index}]} post-budget, deterministic
                          order, DISTINCT ids.
      ``total``         — the injected DISTINCT-item count (== sum of the
                          groups).
      ``dropped_by_cap``— rows the per-session/total budgets dropped.
      ``total_cap_hit`` — the total budget bound at least once (reachable
                          at the shipped defaults — see
                          :data:`DEFAULT_REINJECTION_TOTAL_ITEMS`).

    The budgets count DISTINCT item ids. A graph can hold several
    ``Point`` nodes carrying one ``id`` (a concurrent re-ingest races the
    ingest path's exist-probe — ``ingest._point_exists`` — which cannot be
    atomic across processes); charging one budget slot per row would then
    let duplicates eat a session's whole allowance and inject a single
    item while reporting a full group.
    """
    report: dict[str, Any] = {
        "ok": False, "by_session": {}, "total": 0,
        "dropped_by_cap": 0, "total_cap_hit": False}
    seeds = [s for s in dict.fromkeys(seed_point_ids) if s]
    if not seeds or per_session_cap < 1 or total_cap < 1:
        report["ok"] = True
        return report
    kind_filter = (f"  AND {_TURN_SHAPE_FILTER} "
                   if chunk_kind == TURN_POINT_KIND else "")
    try:
        rows = proj.g.query(
            "MATCH (seed:Point) WHERE seed.id IN $seed_ids "
            # #6976: load-bearing `WITH seed` — without it FalkorDB 6.0.0 drops
            # the id predicate at the re-binding MATCH below (foreign rows).
            "WITH seed "
            "MATCH (s:Session)-[:CONTAINS]->(seed) "
            # ``WITH DISTINCT s`` is a PLAN barrier, not a result change: it
            # dedups the session reached from a shared seed and forces the
            # driver to be the seed list (GRAPH.EXPLAIN: seed scan → traverse
            # → Distinct → traverse) instead of a full ``Point`` label scan
            # with the seed filter applied last (#2517 review).
            "WITH DISTINCT s "
            "MATCH (s)-[:CONTAINS]->(p:Point) "
            "WHERE coalesce(p.pointKind, '') = $chunk_kind "
            + kind_filter +
            "  AND NOT p.id IN $pool_ids "
            "RETURN p.id, coalesce(p.session_id, s.id), "
            "       coalesce(p.lme_chunk_index, -1) "
            "ORDER BY coalesce(p.session_id, s.id), "
            "         coalesce(p.lme_chunk_index, -1), p.id",
            params={"seed_ids": list(seeds), "chunk_kind": chunk_kind,
                    # the call site holds a SET — bind a list (every repo
                    # precedent passes a list; FalkorDB params are typed).
                    "pool_ids": list(pool_ids)},
        ).result_set
    except Exception:  # noqa: BLE001, RUF100
        logger.warning(
            "C4 source-session fetch failed — keeping the original pool "
            "(fail-open)", exc_info=True)
        return report
    per: dict[str, int] = {}
    by_session: dict[str, list[dict]] = {}
    total = 0
    dropped = 0
    total_cap_hit = False
    seen_pids: set[str] = set()
    for pid, sid, idx in rows or []:
        sid = str(sid or "")
        pid = str(pid or "")
        if not sid or not pid or pid in seen_pids:
            continue
        if total >= total_cap:
            total_cap_hit = True
            dropped += 1
            continue
        if per.get(sid, 0) >= per_session_cap:
            dropped += 1
            continue
        seen_pids.add(pid)
        by_session.setdefault(sid, []).append(
            {"id": pid, "session_id": sid, "lme_chunk_index": idx})
        per[sid] = per.get(sid, 0) + 1
        total += 1
    report.update(ok=True, by_session=by_session, total=total,
                  dropped_by_cap=dropped, total_cap_hit=total_cap_hit)
    return report


def reinjection_merge_order(
        pool: list[dict],
        added_by_session: Mapping[str, list[dict]], *,
        seed_order: list[str] | None = None,
        session_key: Callable[[dict], str] | None = None,
        guard: bool = True,
        guard_window: int = DEFAULT_POOL_GUARD_WINDOW,
        per_session_cap: int = DEFAULT_POOL_SESSION_CAP,
        floor_term: bool = False,
        max_chunks_per_session: int) -> list[dict]:
    """MERGE (pure, additive): splice each seeded session's injected group
    immediately after that session's LAST base rank in the pool, in
    seed-rank order; drop ids already present; then apply the shared
    guard + C5 re-cap (``retrieval.guard_and_recap_pool``).

    The anchor is the session's last base rank IN THE POOL, not merely in
    the seed window — so the injected group lands after every base hit of
    that session and the C5 re-cap keeps base chunks first. With the guard's
    default (``floor_term=False``) an injected item can therefore never evict
    a base item — the ``floor_term`` arm below WITHDRAWS that property. With
    no genuinely-new id the base pool is returned unchanged (the caller's
    ``new_ids`` gate is mirrored here for defence in depth).

    ⚠️ Pool-additivity is NOT reader-additivity (#10540). The reader
    consumes a PREFIX (``context_item_cap``, default 40), so an injected
    item inside that window pushes one base hit past the cut — measured at
    the shipped budgets: 10 base hits displaced and TWO sessions losing
    their entire representation. That trade is what
    :func:`reader_window_displacement` measures. The older wording here
    ("an injected item can never evict a base item") was true of the pool
    LIST and false of the reader, which is why the crowding was invisible
    to the census.

    ⚠️ REBASE UNION (#10632 × #2513): both sides rewrote this
    paragraph. The folded tree added the ``floor_term`` precision (the arm
    THAT CAN evict a base item); this PR added the reader-window disclosure
    (pool-additivity ≠ reader-additivity). They describe different
    mechanisms and both are true, so both are kept — dropping either would
    restore a claim the other side measured to be false.

    NOTE: ``guard_and_recap_pool``'s C5 re-cap counts only ``is_raw_chunk``
    hits, so injected TURN points (the default kind) are not re-capped —
    the caller's total budget is the volume guard on that path.

    ``floor_term`` (#2513) forwards to the guard's missing LOWER bound
    unchanged (see ``coverage_loop.session_diverse_order``). It is OFF by
    default and default-preserving. It is forwarded from HERE as well as
    from ``guard_and_recap_pool`` because this is the second composition the
    eval lane calls (``tools/longmem_eval/retrieve.py`` uses both): an A/B
    that could arm the direct composition only would leave the reinjection
    leg unmeasurable, which is the one thing the default-off seam exists to
    make possible.

    ⛔ With ``floor_term`` ON, the no-eviction property above does NOT hold:
    an injected item from a session holding NO window slot may take a slot
    back from a session holding more than one, and that victim can be a BASE
    item. That spend IS the arm's measured density cost — the reason it is
    off by default — not an artifact, so a measurement lane that trusts the
    unqualified promise would charge it to the wrong place.
    """
    if not pool:
        return list(pool)
    key = session_key if session_key is not None else session_key_of
    order = list(seed_order) if seed_order is not None else list(added_by_session)
    last_rank: dict[str, int] = {}
    for i, hit in enumerate(pool):
        last_rank[key(hit)] = i
    seen: set[str] = {h["id"] for h in pool}
    groups: dict[str, list[dict]] = {}
    for sid in order:
        keep: list[dict] = []
        for hit in (added_by_session.get(sid) or []):
            hid = hit.get("id")
            if not hid or hid in seen:
                continue
            seen.add(hid)
            keep.append(hit)
        if keep:
            groups[sid] = keep
    if not groups:
        return list(pool)
    merged: list[dict] = []
    for i, hit in enumerate(pool):
        merged.append(hit)
        for sid in order:
            if last_rank.get(sid) == i and sid in groups:
                merged.extend(groups.pop(sid))
    # Defensive tail (a seeded session with no base rank could not come
    # from ``seeded_sessions``; keep the hits rather than silently drop).
    for sid in order:
        if sid in groups:
            merged.extend(groups.pop(sid))
    return guard_and_recap_pool(
        merged, guard=guard, session_key=key, window=guard_window,
        per_session_cap=per_session_cap, floor_term=floor_term,
        max_chunks_per_session=max_chunks_per_session)


def reader_window_displacement(
        base_pool: list[dict],
        merged: list[dict], *,
        window: int,
        session_key: Callable[[dict], str] | None = None) -> dict:
    """What the merge took from OTHER sessions in the READER's window (#10540).

    :func:`reinjection_merge_order` is ADDITIVE on the POOL — it never drops
    a base hit — but the reader consumes a PREFIX of that pool
    (``context_item_cap``, default 40). An injected item is therefore free
    in the pool and NOT free in the reader: every injected item inside the
    window pushes one base hit past the cut, and the sessions whose hits sat
    at the window's tail can lose their representation ENTIRELY — the exact
    crowding this arm's outcome forbids.

    Measured on ``63981d152`` (40 base hits over 8 sessions, 5 each, shipped
    budgets — per-session 3, total 10, guard window 5, per-session cap 2):
    ``base_displaced=10``, ``sessions_lost=['s6', 's7']`` — the two
    lowest-ranked sessions lose all five of their hits each while
    ``injected_merged`` reports a healthy 10. With no injection the same
    measurement is 0, so the number is attributable to the injection.

    Four residuals, stated so the number is not read as more than it is:

    * ``base_pool`` is the RAW pre-merge list while ``merged`` has been
      through ``guard_and_recap_pool``, so ``base_displaced`` also counts
      the C4 guard's OWN window reorder and cannot separate
      injection-caused crowding from guard-caused moves. Pass the
      guard-applied counterfactual (``guard_and_recap_pool(base_pool, ...)``)
      if that distinction matters.
    * When the base pool FILLS the window, ``base_displaced`` equals
      ``injected_in_window`` algebraically — one injected item in, one base
      item out. They are corroborating only in the partial-window case.
    * The window counted here is the RAW prefix of ``merged``, while
      ``assemble_context`` admits a SKIP-FILTERED subsequence: a
      claim-text-less hit spends no item slot and a cap-failing hit is
      skipped without one (``tortoise/retrieval.py``), and with the
      evidence-package arm on, the reader gets a reordered, deduplicated
      list. The count is also taken BEFORE the C2 evidence-mark boost
      (``_apply_evidence_boost``, which the eval lane applies to the pool
      AFTER this census and before ``_assemble_context``), so with
      ``TORTOISE_LME_EVIDENCE_BOOST`` on — the re-validation run's own
      default — the census window is the PRE-boost order and the reader's
      is the post-boost one.
    * ONE LATER POOL MOVER IN THE SAME LANE IS NOT IN THIS COUNT, and it is
      the re-SELECTION only (``tools/longmem_eval/retrieve.py``). When R6
      rerank APPLIES it re-SELECTS from the whole pool (``pool = selected``,
      ``<= top_k``), and C6 ``prefer_latest_order`` reorders the final pool.
      ``prefer_latest_order`` is membership-preserving, but the reranker's
      move is a fresh SELECTION over every candidate — a two-stage
      retrieve→rerank pipeline picks its ``top_n`` from the FULL candidate
      list, not from a prefix of it (Cohere's Rerank API:
      ``rerank(documents=<all candidates>, top_n=k)`` returns indices into
      that whole list), and the lane's own ``rerank_hits`` is called on
      the entire ``pool``. So on the APPLIED arm the delivered surface may
      include base hits AND injected items from BELOW this window while
      excluding ones inside it.
      ⇒ On the APPLIED arm ``injected_in_window``, ``base_displaced`` and
      ``sessions_lost`` describe ``merged[:window]`` and are not a bound at
      all — upper or lower — on what the reader got. ``sessions_lost`` is
      derived from those SAME two windows, so the reranker can deliver a
      session ranked below ``merged[:window]`` while this census names it
      LOST: on that arm the key OVER-reports. (Nor are they a bound with R6
      OFF, for the reason the bullet below gives.)
      ⛔ The three TRUNCATING arms are NOT in that class, and an earlier
      revision of this list wrongly put one of them there: the pool-only
      arm and the two R6 DEGRADES (load-failure ``scorer is None``,
      score-failure ``applied: False``) each cut a prefix of the pool, and
      the caller declares that cut so ``window`` becomes the cut
      (``min(item_cap, len(pool))`` after it — #10632 review ISSUE 1). No
      candidate outside the window can reach the reader on those arms, so
      there the window IS a ceiling. It is still not the DELIVERED set:
      the skip filter below can only remove items from inside it.
    * So the count is not a bound on the delivered surface in EITHER
      direction, and the skip semantics above are why in both: a
      claim-text-less (or cap-refused) hit INSIDE the window is counted and
      never delivered, so the number can OVER-state what the reader got —
      while a skipped hit FREES its slot, so ``assemble_context`` continues
      PAST the raw prefix and admits later hits up to its item bound, and
      injected items BELOW the window can therefore be delivered without
      ever being counted. That under-count is the direction this key exists
      for. It is a WINDOW CENSUS, not a ceiling and not a floor. This
      module already states the same residual class for ``seeded_sessions``
      (*"matches the reader MODULO the budget caps"*); a reuse at a
      different window, or alongside the packaging, boost or rerank arms,
      needs the same ``admitted=``/counterfactual treatment before the
      number is read as delivered evidence.

    ⛔ The TOP-5 is NOT where this happens, and a probe that counts base
    hits at final rank ``>= 5`` says otherwise: it reports 35 for that shape
    with 0, 10 or 15 injected items, because 35 of 40 base hits start below
    rank 5. That figure is invariant to injection and is not a crowding
    measurement. The guard's session-diverse reorder holds the top-5
    (measured: 0 base items lost from it in every arm tested).

    ``sessions_lost`` is the load-bearing key on every arm that does not
    re-SELECT: a session with no hit in the merged window has no evidence the
    reader can cite, whatever its rank was before. On the APPLIED-rerank arm
    it is an OVER-report rather than a bound, for the reason the bullet above
    gives — the reranker selects from the whole candidate list, so a session
    ranked below this window can be delivered while this census names it
    lost.

    The synthetic ``idx:*`` namespace is dropped from BOTH sets: it names no
    graph session (the ``lme_session_index`` bucket, and since #3591 the
    per-hit ``idx:point:{id}`` bucket of a hit with no session identity), so
    a phantom must never be reported as a session PRESENT in the window nor
    as one LOST from it — the falsest possible ``sessions_lost`` entry, since
    the reader could not have cited it in the first place. This is the same
    exclusion ``coverage_loop.coverage_gap`` applies to its window census,
    and ``retrieval.session_key_of`` documents the namespace as phantom. The
    filter is applied to the resulting SETS, not to the key function: a
    caller-supplied ``session_key`` cannot drop a bucket, only remap one.

    Returns ``{"window", "injected_in_window", "base_displaced",
    "sessions_lost", "sessions_before", "sessions_after", "measured"}`` —
    ``measured`` is ``True`` on every path this function returns, and the
    CALLER sets it ``False`` (with ``None`` values) when its own call fails,
    so a consumer can tell an unmeasured question from a zero. Additive and
    pure — the caller records it; nothing here reorders or drops.
    """
    key = session_key if session_key is not None else session_key_of
    if window < 1:
        return {"window": 0, "injected_in_window": 0, "base_displaced": 0,
                "sessions_lost": [], "sessions_before": 0,
                "sessions_after": 0, "measured": True}
    base_ids = {h["id"] for h in base_pool}
    base_window = list(base_pool[:window])
    merged_window = list(merged[:window])
    merged_window_ids = {h["id"] for h in merged_window}
    injected_in_window = sum(1 for h in merged_window
                             if h["id"] not in base_ids)
    base_displaced = sum(1 for h in base_window
                         if h["id"] not in merged_window_ids)
    before = {k for k in (key(h) for h in base_window)
              if k and not k.startswith("idx:")}
    after = {k for k in (key(h) for h in merged_window)
             if k and not k.startswith("idx:")}
    return {
        "window": window,
        "injected_in_window": injected_in_window,
        "base_displaced": base_displaced,
        # sorted: a census key must not depend on pool order
        "sessions_lost": sorted(before - after),
        "sessions_before": len(before),
        "sessions_after": len(after),
        "measured": True,
    }
