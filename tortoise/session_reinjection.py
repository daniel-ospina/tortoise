"""C4 (#2517 / #2568, #2513): source-session re-injection on seeded hits.

The reader-surface + pool-rank-cut lever for the multi-session partial-
evidence bucket (docs/scoping/2026-09-07-2513-multisession-evidence-
surface.md §4 C4): a seeded hit's own source session has raw
``session-transcript`` chunks that are question-relevant but rank below
the reader window, so the verbatim evidence never reaches the reader
(and, for the all-or-nothing ``recall_all@5`` binary, a pool-present
starved session sits at rank >= 6). This module ships the product rules
as pure primitives plus ONE bounded graph pass:

1. **SEED** — :func:`seeded_sessions`: the distinct REAL ``session_id``
   values represented in the reader-reachable pool head, in first-seen
   rank order, bounded by ``limit``. Label-free: the trigger is RANK, never
   a stored/read-time mark (the mark-triggered variant was rejected as
   gold leakage — the product has no such mark; #2513 §1.1). The
   synthetic ``idx:N`` bucket key is dropped (it can never equal a graph
   ``p.session_id``, so seeding it would be a phantom session).
2. **EXPAND** — :func:`source_session_chunk_pass`: ONE batched Cypher
   fetch per FIRED question (never one per seed), bounded by a
   per-session cap and a total-item cap. The pool-membership filter lives
   IN the query (``NOT p.id IN $pool_ids``) so the budgets are spent on
   genuinely-new chunks; ``lme_question_id`` is unconditional (prevents
   cross-question ``session_id`` collisions). Fail-open: any failure
   yields the empty result and the caller keeps the ORIGINAL pool.
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
    SESSION_TRANSCRIPT_KIND,
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

#: Injected chunks per seeded session (the per-session budget).
DEFAULT_REINJECTION_PER_SESSION = 3

#: Injected chunks per fired question, across all seeds (the total budget).
#:
#: ⚠️ Under the shipped defaults this budget can NEVER bind: the fan-out
#: is ``SEED_SESSIONS * PER_SESSION = 5 * 3 = 15`` distinct chunks (a
#: duplicate id spends no slot), so ``total >= total_cap`` is unreachable
#: and ``source_session_chunk_pass``'s returned ``total_cap_hit`` is
#: ALWAYS False. With the defaults, ``dropped_by_cap`` therefore counts
#: PER-SESSION drops only. This constant is HEADROOM for a caller that
#: raises ``per_session_cap``/``limit`` — do not read ``total_cap_hit``
#: from a default-configured eval run as a live signal.
DEFAULT_REINJECTION_TOTAL_ITEMS = 20


@dataclass(frozen=True)
class SeededSession:
    """One seeded session: its ``session_id`` and its first-seen pool rank."""
    session_id: str
    rank: int


def _is_real_session_id(key: str) -> bool:
    """A seed must be a REAL ``session_id``. The synthetic ``idx:N`` bucket
    key (and the ``""`` sentinel) can never equal a graph ``p.session_id``,
    so seeding one would spend the fetch budget on a phantom session."""
    return bool(key) and not key.startswith("idx:")


def seeded_sessions(pool: list[dict], *,
                    window: int = DEFAULT_REINJECTION_SEED_WINDOW,
                    limit: int = DEFAULT_REINJECTION_SEED_SESSIONS,
                    session_key: Callable[[dict], str] | None = None,
                    ) -> list[SeededSession]:
    """SEED (pure, label-free): the distinct real sessions represented in
    ``pool[:window]``, in first-seen rank order, at most ``limit``.

    Deterministic and label-free — the only signal is the pool rank the
    retrieval engine already produced. ``window`` is the reader-reachable
    head (the eval derives it from the resolved reader item cap);
    ``limit`` bounds the fan-out.
    """
    if not pool or window < 1 or limit < 1:
        return []
    key = session_key if session_key is not None else session_key_of
    win = max(1, min(window, len(pool)))
    out: list[SeededSession] = []
    seen: set[str] = set()
    for rank, hit in enumerate(pool[:win]):
        sid = key(hit)
        if not _is_real_session_id(sid) or sid in seen:
            continue
        seen.add(sid)
        out.append(SeededSession(session_id=sid, rank=rank))
        if len(out) >= limit:
            break
    return out


def source_session_chunk_pass(
        proj: Any, session_ids: list[str], *,
        question_id: str,
        pool_ids: Any,
        chunk_kind: str = SESSION_TRANSCRIPT_KIND,
        per_session_cap: int = DEFAULT_REINJECTION_PER_SESSION,
        total_cap: int = DEFAULT_REINJECTION_TOTAL_ITEMS,
        ) -> dict[str, Any]:
    """EXPAND (one bounded graph pass per fired question, fail-open).

    Fetches the raw ``session-transcript`` chunks of the seeded sessions
    that are NOT already in the pool, for THIS question only, in a
    deterministic ``(session_id, lme_chunk_index, id)`` order, then applies
    the per-session and total budgets IN that order.

    Returns a report dict (never raises):
      ``ok``            — the query ran (False on any failure).
      ``by_session``    — {session_id: [{id, session_id, lme_chunk_index}]}
                          post-budget, deterministic order, DISTINCT ids.
      ``total``         — the injected DISTINCT-chunk count (== sum of the
                          groups).
      ``dropped_by_cap``— rows the per-session/total budgets dropped.
      ``total_cap_hit`` — the total budget bound at least once.

    The budgets count DISTINCT chunk ids. A graph can hold several
    ``Point`` nodes carrying one ``id`` (a concurrent re-ingest races the
    ingest path's exist-probe — ``ingest._point_exists`` — which cannot be
    atomic across processes); charging one budget slot per row would then
    let duplicates eat a session's whole allowance and inject a single
    chunk while reporting a full group.
    """
    report: dict[str, Any] = {
        "ok": False, "by_session": {}, "total": 0,
        "dropped_by_cap": 0, "total_cap_hit": False}
    sids = [s for s in dict.fromkeys(session_ids) if s]
    if not sids or per_session_cap < 1 or total_cap < 1:
        report["ok"] = True
        return report
    try:
        rows = proj.g.query(
            "MATCH (p:Point) "
            "WHERE p.session_id IN $sids "
            "  AND coalesce(p.pointKind, '') = $chunk_kind "
            "  AND p.lme_question_id = $q "
            "  AND NOT p.id IN $pool_ids "
            "RETURN p.id, p.session_id, coalesce(p.lme_chunk_index, -1) "
            "ORDER BY p.session_id, coalesce(p.lme_chunk_index, -1), p.id",
            params={"sids": list(sids), "chunk_kind": chunk_kind,
                    "q": question_id,
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
    chunk therefore can never evict a base chunk. With no genuinely-new id
    the base pool is returned unchanged (the caller's ``new_ids`` gate is
    mirrored here for defence in depth).
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
