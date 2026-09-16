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
   values represented in a conservative RANK-WINDOW approximation of the
   reader-reachable pool head (``assemble_context`` skips claim-text-less
   hits without spending a slot, #2978 — so this under-seeds by at most
   those skipped hits), in first-seen rank order, bounded by ``limit``. Label-free: the trigger is RANK, never
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
   product actually writes (``TortoiseSDK.capture_session``,
   ``hosted POST /v1/sessions``) — NOT ``session-transcript``, which is
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

   ⚠️ SEED identity limitation (unchanged by this retarget). The fetch
   TARGET is product-real; the SEED gate is not, for a turn hit. A product
   turn Point carries no ``session_id``, so ``session_key_of`` returns the
   synthetic ``idx:-1`` and :func:`seeded_sessions` drops it as a phantom
   bucket. In the product the seed is therefore a NON-turn pool hit that
   carries ``session_id`` (an extracted point — ``capture_session`` links
   it ``Session-[:CONTAINS]->Point`` and stamps ``session_id``), and the
   fetch then expands that session's verbatim turns. Letting a turn hit
   seed itself needs a product-side session identity for turn points; that
   is a SEED change, out of scope for the fetch retarget.

   **Turn-shape constraint.** ``pointKind``'s vocabulary is open
   (``create_point`` accepts any registered kind), so ``pointKind='event'``
   alone does not prove a TURN: the hosted demo/dashboard seed writes
   ``pointKind='event'`` points with NO ``is_episodic`` — their body IS
   ``[role]``-tagged, so the ``is_episodic`` conjunct is what excludes them
   (``hosted_api.py:~6243``) — and any SDK/API caller can mint one.
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

#: The default seed window: a conservative rank-window approximation of
#: the reader-reachable pool head. The eval derives the effective window
#: from the resolved reader item cap (``eff_item_cap``) so a non-default
#: ``TORTOISE_LME_CONTEXT_ITEMS`` cannot silently desynchronise it; this
#: constant is the product fallback.
#:
#: NOT exact: ``retrieval.assemble_context`` SKIPS claim-text-less hits
#: (#2978, decoration-only nodes such as operators) without consuming an
#: item slot, so the reader may admit hits BELOW rank ``window``. This
#: window can therefore under-seed — a session whose first pool appearance
#: falls in a skipped-hit gap is reader-reachable yet unseeded. Widening it
#: is a deliberate conservative bound, not an identity claim (tracked as a
#: follow-up; changing it is a measurement-validity change, not a fix).
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
#:   * every TURN writer (``TortoiseSDK.capture_session`` `sdk.py:~3351`,
#:     the hosted turn loop `hosted_api.py:~8227`, and the eval legs
#:     `ingest.py:~207` / `ingest_v2.py:~543`) writes ``is_episodic=true``
#:     AND a body of the deterministic form ``f"[{role}] {content}"`` — so
#:     the body always STARTS WITH ``[``;
#:   * the NON-turn ``pointKind='event'`` writer in the tree — the
#:     hosted demo/dashboard seed (`hosted_api.py:~6243`) — writes no
#:     ``is_episodic`` (its body IS ``[role]``-tagged, so the shape
#:     predicate excludes it on the ``is_episodic`` conjunct alone);
#:   * so this predicate excludes it and any caller-minted bare ``event``
#:     Point, while holding for every legitimate turn.
#:
#: The check is deliberately NOT ``speaker IS NOT NULL``: the ``speaker``
#: property arrived with delta 5 (#721) and set it alongside, so requiring
#: it would silently exclude legacy turns for no additional exclusion power
#: (the demo seed's exclusion is already carried by ``is_episodic``).
_TURN_SHAPE_FILTER = ("coalesce(p.is_episodic, false) = true "
                      "AND coalesce(p.content, '') STARTS WITH '['")


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
    on a group that could only land in the defensive tail."""
    return bool(key) and not key.startswith("idx:")


def seeded_sessions(pool: list[dict], *,
                    window: int = DEFAULT_REINJECTION_SEED_WINDOW,
                    limit: int = DEFAULT_REINJECTION_SEED_SESSIONS,
                    session_key: Callable[[dict], str] | None = None,
                    ) -> list[SeededSession]:
    """SEED (pure, label-free): the distinct real sessions represented in
    ``pool[:window]``, in first-seen rank order, at most ``limit``.

    Deterministic and label-free — the only signal is the pool rank the
    retrieval engine already produced. ``window`` is a CONSERVATIVE
    RANK-WINDOW APPROXIMATION of the reader-reachable head (the eval
    derives it from the resolved reader item cap), not the reader's
    admitted set: :func:`retrieval.assemble_context` skips claim-text-less
    hits (#2978) without spending an item slot, so the reader can admit
    hits below this rank and a session reader-reachable only through such
    a gap is not seeded. ``limit`` bounds the fan-out.

    A hit that carries no ``id`` cannot be seeded: the fetch anchors on the
    seeded hit to resolve its Session (``Session-[:CONTAINS]->hit``), so a
    seed without one would spend the budget on a session the query cannot
    name. Pool hits always carry their id.
    """
    if not pool or window < 1 or limit < 1:
        return []
    key = session_key if session_key is not None else session_key_of
    win = max(1, min(window, len(pool)))
    out: list[SeededSession] = []
    seen: set[str] = set()
    for rank, hit in enumerate(pool[:win]):
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
        no ``session_id``). A key absent from the caller's ``seed_order``
        (reachable only via a Session reached through a shared seed) lands
        in ``reinjection_merge_order``'s defensive tail rather than after
        its own base rank.

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
        max_chunks_per_session: int) -> list[dict]:
    """MERGE (pure, additive): splice each seeded session's injected group
    immediately after that session's LAST base rank in the pool, in
    seed-rank order; drop ids already present; then apply the shared
    guard + C5 re-cap (``retrieval.guard_and_recap_pool``).

    The anchor is the session's last base rank IN THE POOL, not merely in
    the seed window — so the injected group lands after every base hit of
    that session and the C5 re-cap keeps base chunks first. An injected
    item therefore can never evict a base item. With no genuinely-new id
    the base pool is returned unchanged (the caller's ``new_ids`` gate is
    mirrored here for defence in depth).

    NOTE: ``guard_and_recap_pool``'s C5 re-cap counts only ``is_raw_chunk``
    hits, so injected TURN points (the default kind) are not re-capped —
    the caller's total budget is the volume guard on that path.
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
        per_session_cap=per_session_cap,
        max_chunks_per_session=max_chunks_per_session)
