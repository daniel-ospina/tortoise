"""v2 ingest mode for the LongMemEval runner (issue #1369).

Each haystack session goes through the PRODUCTION v2 extractor
(``extractor_v2.extract_session_v2`` — the default BYOK path since #1385,
with supersession extraction since #1394), and the resulting Layer-1 payload
is written to the eval graph: entities, points, events, operators. The
deterministic leg's Session nodes + raw-transcript points are RETAINED
(#1369 indicator 3 — verbatim recall mitigation) so v2 mode is comparable to
the deterministic baseline on the same retrieval.

Evidence marking (the extractor's recall contribution, M6 #1526): the
miscalibrated ``>=0.4`` content-overlap predicate is REPLACED by three
independent marks from the shared ``evidence.py`` module — (a) source-session
attribution (the point's ``session_id`` is an evidence session), (b) verbatim
anchor (the point's ``quote`` contains/overlaps an answer turn; quotes are
populated deterministically at ingest via D3 anchoring since the extractor
emits an empty quote), (c) raw-chunk containment (the session raw transcript
contains an answer turn verbatim). Marks are OR-combined into the eval
``has_answer`` property so the runner's evidence-recall@k measures 'did an
evidence-marked point surface in top-k'.

Combined with the raw-transcript leg (which always matches), this gives the
extractor-vs-retrieval attribution: an evidence-bearing extracted point in
the context but a wrong answer = reader failure; evidence content in the
graph but not retrieved = retrieval failure; evidence content NOT extracted
at all = extractor failure.

MITIGATES operators are recorded in the stats but not written as edges (the
eval measures retrieval/reader, not EP propagation; IMPL/NAND edges carry
the structural signal).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from tortoise.sdk import TortoiseSDK

logger = logging.getLogger(__name__)

from .evidence import (EVIDENCE_QUOTE_CAP, anchor_quote, evidence_sessions,  # noqa: E402, I001
                       mark_for)
from .evidence import _overlap  # noqa: F401, E402 — back-compat re-export
from .ingest import (SESSION_TRANSCRIPT_KIND, EXTRACTION_POINT_KIND,  # noqa: E402
                     UNDATED_SENTINEL,
                     _existing_point_ids, _session_chunks)


def _point_status(proj, pid: str) -> str:
    """The point's persisted status — '' when the point does not exist.
    (E7 D6: supersession endpoint statuses now ride the batch probe — this
    per-id helper is retained for direct callers only.)"""
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status",
        params={"id": pid}).result_set
    return str(rows[0][0] or "") if rows else ""


def _write_payload(sdk: TortoiseSDK, payload: dict, *, sid: str, qid: str,
                   si: int, evidence_turns: list[str],
                   session_date: str | None = None,
                   turns: list[dict], ev_sessions: set[str],
                   gold_answer: str = "", n_turns: int = 0) -> dict:
    """Write a v2 Layer-1 payload into the eval graph. Idempotent per point
    (explicit deterministic ids + _point_exists guard). Returns stats.

    M6 evidence marking: each extracted point gets the OR of the three marks
    (source-session attribution / verbatim quote anchor / raw-chunk
    containment) and a D3-deterministically-anchored quote (the extractor
    emits an empty quote; gate 2: consume a non-empty payload quote
    instead). #1763: the answer-string mark (d) is computed from
    ``gold_answer`` (the dataset question's gold answer — known to the eval
    harness, never the extractor) and recorded SEPARATELY: it is
    census-counted and written as the durable ``answer_string_mark`` point
    property, but NOT OR'd into ``has_answer`` (the legacy denominator must
    not move — D5 #1540 comparability). The per-mark breakdown is counted in
    ``stats["evidence_marks"]`` so the report can say WHY evidence exists."""
    stats = {"entities": 0, "points": 0, "events": 0, "operators": 0,
             "evidence_points": 0, "minted_kinds": 0,
             "supersessions_written": 0,
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0, "answer_string": 0}}
    proj = sdk._get_proj()
    # R5 (#1544): points in a dated session carry the session date as their
    # creation time; undated sessions get the explicit sentinel (never the
    # server default createdAt=now — deterministic-oldest → recency 0.0).
    point_created_at = session_date or UNDATED_SENTINEL

    # E7 (#1539 D6): ONE batch existence probe per payload — the payload's
    # point ids + operator src/dst ids — so the per-point/per-op
    # ``_point_exists`` N+1 collapses to O(1) queries per session at 500-Q
    # run scale. Point-node semantics preserved exactly: event-endpoint
    # operator ids are simply absent from the set (today's behavior —
    # operators over events stay skipped). Supersession endpoints are NOT
    # here: the new point is created by the points loop BELOW, so its
    # existence is probed by the supersession section's own single batch.
    batch_ids: list[str] = [
        str(p.get("id") or "") for p in (payload.get("points") or [])
        if p.get("id")]
    for op in (payload.get("operators") or []):
        batch_ids += [str(op.get("src") or ""), str(op.get("dst") or "")]
    existing = _existing_point_ids(proj, batch_ids)

    # ── entities (objects) ──
    for e in payload.get("entities", []) or []:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        try:
            sdk.create_entity("object", name,
                              objectKind=str(e.get("kind", "core:other")),
                              lme_question_id=qid, lme_session_index=si,
                              is_episodic=True)
            stats["entities"] += 1
        except Exception as ex:  # noqa: BLE001, RUF100
            logger.warning("v2 ingest entity %r failed: %s", name, ex)

    # ── points (the search surface) ──
    # ``created_point_ids`` (E7 D6): the ids the points loop actually
    # creates this run — the operator loop runs AFTER the points loop, so
    # same-payload endpoints are in THIS set (the pre-loop batch ``existing``
    # only covers points that already existed, e.g. re-ingest).
    created_point_ids: set[str] = set()
    for p in payload.get("points", []) or []:
        content = str(p.get("content", "")).strip()
        pid = str(p.get("id", "")).strip()
        if not content or not pid:
            continue
        # D3: deterministic quote population — consume a non-empty payload
        # quote (E3's future wiring, gate 2) capped at EVIDENCE_QUOTE_CAP,
        # else anchor one from the session turns (extractor emits "").
        quote = str(p.get("quote") or "")[:EVIDENCE_QUOTE_CAP]
        if not quote:
            quote = anchor_quote(content, turns)
        mark = mark_for({**p, "content": content, "quote": quote},
                        session_id=sid, evidence_sessions=ev_sessions,
                        answer_turn_contents=evidence_turns,
                        gold_answer=gold_answer)
        if pid in existing:
            # #1369 review P2: content-addressed collision across sessions —
            # OR-in this session's evidence marking (M6: never overwrite a
            # True with False on collision; first-writer props keep the
            # session id; the raw-transcript leg mitigates attribution).
            # #1763: the answer-string mark ORs in the same never-False way.
            if mark["has_answer"]:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": pid})
            if mark["marks"]["answer_string"]:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.answer_string_mark = true",
                    params={"id": pid})
            continue
        try:
            # E3 (D6): resolve the payload's 0-based source_turn_id index to
            # the turn node id (the eval read path derives speaker from it).
            # Bounded to the SESSION's actual turn count (never guess — an
            # index beyond the turns writes no dangling link).
            turn_idx = p.get("source_turn_id")
            turn_ref = (f"lme:{qid}:s{si}:t{turn_idx}"
                        if type(turn_idx) is int and 0 <= turn_idx < n_turns
                        else None)
            point_props: dict = {}
            # E1 (#1533): the payload `when` slot rides onto the node only
            # when non-empty — undated sessions write no `when` prop.
            # E6 (#1538) D3: validFrom = the fact's valid-time start (the
            # `when` slot) — written at creation, undated ⇒ open window.
            if p.get("when"):
                point_props["when"] = str(p.get("when"))
                point_props["validFrom"] = str(p.get("when"))
            sdk.create_point(
                EXTRACTION_POINT_KIND, content, id=pid, session_id=sid,
                lme_question_id=qid, lme_session_index=si,
                is_episodic=True, has_answer=mark["has_answer"],
                # #1763: the durable answer-string mark (d) — written as its
                # own property (NOT folded into has_answer) for forensic /
                # denominator queries on EXTRACTED points. The re-baselined
                # recall numerator must use evidence.answer_string_recall_at_k
                # (content-based — raw chunks never carry this property).
                answer_string_mark=mark["marks"]["answer_string"],
                quote=quote, status="draft",
                search_keys=p.get("search_keys") or None,
                source_turn_id=turn_ref,
                reason=str(p.get("reason") or "NEW"),   # E5: REVISES visible on the node
                createdAt=point_created_at,  # R5: session date (sentinel)
                **point_props,
            )
            # E7 (D7): aboutObject parity — the canonical predicate the
            # classifier's entity gate (D2) keys off in the eval graph
            # (mirrors the hosted §4.2 MERGE; one batched query per point).
            names = [str(n) for n in (p.get("about_entities") or [])
                     if isinstance(n, str) and n.strip()]
            if names:
                proj.g.query(
                    "UNWIND $names AS name "
                    "MATCH (p:Point {id:$pid}), (o:Object {name:name}) "
                    "MERGE (p)-[:aboutObject]->(o)",
                    params={"pid": pid, "names": names})
            stats["points"] += 1
            created_point_ids.add(pid)
            if mark["has_answer"]:
                stats["evidence_points"] += 1
            # the per-mark census counts EVERY mark class independently (not
            # gated on the legacy has_answer OR) — #1763: mark (d) answer-
            # string fires on points the legacy marks miss (a foreign-session
            # point carrying the gold answer), and it must still be counted.
            for mk, fired in mark["marks"].items():
                if fired:
                    stats["evidence_marks"][mk] += 1
        except Exception as ex:  # noqa: BLE001, RUF100
            logger.warning("v2 ingest point %r failed: %s", pid, ex)

    # ── events (decision/occurrence — the timeline) ──
    for ev in payload.get("events", []) or []:
        content = str(ev.get("content", "")).strip()
        if not content:
            continue
        try:
            event_props: dict = {}
            # E1 (#1533): startedAt lands on the node only when non-empty
            # (payload started_at → model startedAt → session date fallback).
            started = (ev.get("started_at") or ev.get("startedAt")
                       or session_date)
            if started:
                event_props["startedAt"] = str(started)
            sdk.create_event(
                content[:80], str(ev.get("eventKind", "core:occurrence"))
                .rsplit(":", 1)[-1],
                sessionId=sid, lme_question_id=qid, lme_session_index=si,
                is_episodic=True, **event_props,
            )
            stats["events"] += 1
        except Exception as ex:  # noqa: BLE001, RUF100
            logger.warning("v2 ingest event failed: %s", ex)

    # ── operators (IMPL/NAND edges; MITIGATES recorded, not written) ──
    for op in payload.get("operators", []) or []:
        op_type = str(op.get("op_type", "")).upper()
        src, dst = str(op.get("src", "")), str(op.get("dst", ""))
        if op_type not in ("IMPL", "NAND") or not src or not dst:
            continue
        if (src not in existing and src not in created_point_ids) \
                or (dst not in existing and dst not in created_point_ids):
            continue
        # #1369 review P2: operator idempotency — create_operator mints a
        # fresh node each call; guard on the (op_type, src, dst) triple
        # (op_type is validated IMPL/NAND — safe to inline as the rel type).
        # NOTE: the batch ``existing`` set must NOT be rebound here — later
        # operators need it for their endpoint membership checks.
        dup_edge = proj.g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$t}})-[:{op_type} {{idx:0}}]->(s) "
            f"WHERE s.id = $src "
            f"MATCH (o)-[:{op_type} {{idx:1}}]->(d) WHERE d.id = $dst "
            "RETURN count(*) LIMIT 1",
            params={"t": op_type,
                    "src": src, "dst": dst}).result_set
        if dup_edge and dup_edge[0][0]:
            continue
        try:
            sdk.create_operator(op_type, src, [dst],
                                direction="unidirectional",
                                promote_source=False)
            stats["operators"] += 1
        except Exception as ex:  # noqa: BLE001, RUF100
            logger.warning("v2 ingest operator %s->%s failed: %s",
                           src, dst, ex)

    # ── supersessions (E5 #1537): materialize point-level records via the
    # EXISTING canonical supersede() — CORRECTS edge + outdated + edge
    # transfer. Runs AFTER the points loop so the new point exists (ordering
    # contract, mirrored from the hosted §6b loop). Unresolvable endpoints /
    # already-terminal olds are skipped with a warning (idempotent re-ingest;
    # supersede_point would raise on a terminal old). ONE batched probe
    # (id + status) covers every endpoint — no per-record N+1 (D6). ──
    ss_records = [sr for sr in (payload.get("supersessions") or [])
                  if isinstance(sr, dict)]
    ss_existing: dict[str, str] = {}
    if ss_records:
        ss_endpoints: list[str] = []
        for sr in ss_records:
            old_id = str(sr.get("superseded") or "").strip()
            new_id = str(sr.get("supersedes_by") or "").strip()
            if old_id.startswith("pt_") and new_id.startswith("pt_")\
                    and old_id != new_id:
                ss_endpoints += [old_id, new_id]
        if ss_endpoints:
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, n.status",
                params={"ids": ss_endpoints}).result_set
            ss_existing = {r[0]: (r[1] or "") for r in rows}
    for sr in ss_records:
        old_id = str(sr.get("superseded") or "").strip()
        new_id = str(sr.get("supersedes_by") or "").strip()
        if not (old_id.startswith("pt_") and new_id.startswith("pt_")) \
                or old_id == new_id:
            continue
        if old_id not in ss_existing or new_id not in ss_existing:
            logger.warning("v2 supersession skip %s→%s: endpoint missing "
                           "(fail-open)", old_id, new_id)
            continue
        if ss_existing[old_id] in ("superseded", "retracted", "archived"):
            continue   # idempotent re-ingest — already terminal
        try:
            sdk.supersede(old_id, new_id)     # EXISTING canonical unified tool
            stats["supersessions_written"] += 1
        except Exception as ex:  # noqa: BLE001, RUF100 — best-effort in the eval
            logger.warning("v2 supersede %s→%s failed: %s", old_id, new_id, ex)

    stats["minted_kinds"] = len(payload.get("minted_kinds", []) or [])
    return stats


def _apply_noops(sdk: TortoiseSDK, noops: list[dict], *, s_node: str,
                 has_evidence: bool) -> int:
    """D4 write path: apply result-level NOOP records to the eval graph.

    For each record — the folded session's ref is stamped onto the CANONICAL
    point (additive ``duplicates`` list property, set-merge — idempotent:
    a re-run appends nothing new), the Session gets the link-only CONTAINS
    edge (existing edge types only — NO new edge type, NO IMPL), and when
    the folded session carried an answer turn ``has_answer`` is OR'd onto
    the canonical point (evidence-marking OR-in, mirrors the #1369 P2
    collision OR-in). Physically ONE point → retrieval dedup by
    construction (E2E-11 no-double-count). Best-effort: any failure is
    warned and skipped. Returns the count applied."""
    proj = sdk._get_proj()
    applied = 0
    for rec in noops or []:
        pid = str(rec.get("point_id") or "").strip()
        ref = str(rec.get("session_ref") or s_node or "").strip()
        if not pid:
            continue
        try:
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) "
                "SET p.duplicates = coalesce(p.duplicates, []) + "
                "    CASE WHEN $ref IN coalesce(p.duplicates, []) "
                "         THEN [] ELSE [$ref] END "
                "RETURN p.id",
                params={"id": pid, "ref": ref}).result_set
            if not rows:
                logger.warning("v2 noop target %s missing — skipped", pid)
                continue
            if has_evidence:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": pid})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                "MERGE (s)-[:CONTAINS]->(p)",
                params={"sid": s_node, "pid": pid})
            applied += 1
        except Exception as ex:  # noqa: BLE001, RUF100 — best-effort in the eval
            logger.warning("v2 noop stamp on %s failed: %s", pid, ex)
    return applied


def _apply_deletions(sdk: TortoiseSDK, deletions: list[dict]) -> int:
    """D5 write path: apply result-level DELETE-soft records via the
    EXISTING canonical ``retract_point`` — status='retracted' tombstone
    (point stays in the graph; no resurrect on recall by construction —
    default retrieval excludes terminal statuses, #1391). Best-effort:
    a missing/already-terminal point raises ValueError → warned, the eval
    never dies on a delete. Returns the count applied."""
    applied = 0
    for rec in deletions or []:
        pid = str(rec.get("point_id") or "").strip()
        if not pid:
            continue
        try:
            sdk.retract_point(pid)
            applied += 1
        except ValueError as e:
            logger.warning("v2 deletion %s skipped (fail-open): %s", pid, e)
    return applied


def ingest_haystack_v2(sdk: TortoiseSDK, question: dict,
                       model: Any, *, chunk_turns: int = 2,
                       session_workers: int = 1,
                       model_factory: Callable | None = None) -> dict:
    """v2 ingest: each haystack session through extract_session_v2 → the
    payload written to the eval graph (Session + turn-granular raw chunks
    retained — the verbatim recall mitigation). Returns stats for
    provenance (mirrors ingest_haystack's shape). ``chunk_turns`` (R1
    #1540) is the turns-per-window granularity of the raw chunks (>= 1).

    ``session_workers`` (pilot #1549 — session-parallelism): when > 1, the
    LLM extraction (the wall-clock dominant phase) runs across the sessions
    of THIS question in parallel (the DeepSeek API sustains ~11 calls/s at
    16 concurrent — measured). Graph writes stay sequential (thread-safe by
    construction): phase A writes the session nodes + turn/chunk raw leg for
    ALL sessions, phase B extracts in parallel (each worker uses its own
    ``model_factory()`` model so the RoutingModel's mutable route/truncation
    state is never shared across threads), phase C writes each payload +
    consolidation records. ``model_factory`` (callable → fresh model) is
    REQUIRED for session_workers > 1; otherwise the shared model is used.
    """
    from tortoise.extractor_v2 import _classify_error, extract_session_v2

    qid = question["question_id"]
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    dates: list[str] = question.get("haystack_dates") or []
    ids: list[str] = question.get("haystack_session_ids") or []
    stats = {"sessions": 0, "turns": 0, "chunks": 0, "points": 0,
             "events": 0, "entities": 0, "operators": 0, "evidence_points": 0,
             "evidence_turns": 0, "minted_kinds": 0, "supersessions": 0,
             "supersessions_written": 0,
             "noops_applied": 0, "deletions_applied": 0,
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0, "answer_string": 0}, "errors": [],
             # M4 (#1524, D4): the per-question error census — rolled up from
             # each session's extractor ``error_census`` + the session-level
             # exception class; feeds outcome ``valid``/``error_classes``.
             "error_census": {}}
    # M6: the evidence-session id set (haystack sessions containing >=1
    # has_answer turn) + ALL answer-turn contents (question-wide — marks
    # (b)/(c) match against every answer turn, wherever it lives).
    ev_sessions = evidence_sessions(question)
    all_evidence_turns = [
        str(t.get("content") or "")
        for session in sessions for t in session if t.get("has_answer")]
    # #1763: the GOLD ANSWER STRING (mark (d)) — a benchmark truth the eval
    # harness holds via the question dict (the extractor LLM never sees it),
    # so answer-string marks are computed at eval-ingest time, never at
    # extraction. (Duplicate ingest_haystack_v2 — the live sequential copy
    # below shadows this parallel one, tracked as #1744; kept in sync.)
    gold_answer = str(question.get("answer") or "")

    # ── Phase A (sequential, fast): session nodes + turn/chunk raw leg for
    # ALL sessions — written BEFORE any extraction so verbatim retention +
    # containment marks survive an extractor failure, AND so every session's
    # S3 search sees the full raw graph (cross-session linking, E7). ──
    ctxs: list[dict] = []
    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"
        evidence_turns = [str(t.get("content") or "") for t in session
                          if t.get("has_answer")]
        stats["evidence_turns"] += len(evidence_turns)
        # R5 (#1544): points in a dated session carry the session date as
        # their creation time; undated sessions get the explicit sentinel.
        point_created_at = session_date or UNDATED_SENTINEL

        # ── Session node (mirrors the deterministic leg) ──
        sdk._get_proj().g.query(
            "MERGE (s:Session {id:$id}) "
            "SET s.created_at=coalesce(s.created_at, $ts), "
            "    s.turn_count=$tc, s.is_episodic=true, s.lme_question_id=$qid, "
            "    s.lme_session_index=$si, s.lme_source_session_id=$sid",
            params={"id": s_node, "ts": session_date or _now_iso(), "tc": len(session),
                    "qid": qid, "si": si, "sid": sid},
        )
        stats["sessions"] += 1
        proj = sdk._get_proj()

        # E7 (#1539 D6): ONE batch existence probe per session (turn ids +
        # raw chunk ids) — the per-turn/per-chunk ``_point_exists`` N+1
        # collapses to O(1) queries per session at 500-Q run scale.
        turn_ids = [f"lme:{qid}:s{si}:t{ti}" for ti in range(len(session))]
        session_chunks = list(_session_chunks(session, chunk_turns))
        chunk_ids = [f"lme:{qid}:s{si}:c{ci}" for ci, _, _ in session_chunks]
        existing = _existing_point_ids(proj, turn_ids + chunk_ids)

        # ── E3 (D8): turn points — the speaker-derivation substrate. Same
        # deterministic ids + speaker property as the v1 leg; has_answer is
        # NOT set (v2 turn/evidence recall measures extracted points). ──
        for ti, turn in enumerate(session):
            role = str(turn.get("role") or "unknown")
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            if turn_id not in existing:
                sdk.create_point(
                    "event", f"[{role}] {turn.get('content') or ''!s}",
                    id=turn_id, session_id=sid, lme_question_id=qid,
                    lme_session_index=si, speaker=role,
                    is_episodic=True, status="draft",
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": turn_id})

        # ── Raw verbatim turn-granular chunks (R1 #1540) + containment
        # marks (M6, mark c). Written BEFORE extraction so verbatim
        # retention + marks survive an extractor failure on this session
        # (fail-closed — the raw evidence leg is never silently lost). ──
        for ci, text, turn_idxs in session_chunks:
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            contains_evidence = any(
                bool(turn.get("has_answer"))
                for ti, turn in enumerate(session) if ti in turn_idxs)
            if chunk_id not in existing:
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    has_answer=contains_evidence, status="draft",
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
                stats["chunks"] += 1  # written (post-guard) — stats == graph
            elif contains_evidence:
                # Idempotent OR-in: never overwrite a True with False.
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": chunk_id})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": chunk_id},
            )

        ctxs.append({
            "si": si, "sid": sid, "session_date": session_date,
            "s_node": s_node, "session": session, "evidence_turns": evidence_turns,
        })

    # ── Phase B (parallel): the LLM extraction — the wall-clock dominant
    # phase (pilot #1549 measured ~15-90s/session vs ~1s of graph writes).
    # Each worker uses its OWN model (model_factory) so the RoutingModel's
    # mutable route/last_finish_reason state is never shared across threads.
    # The sdk is shared for S3 reads — FalkorDBLite/redis commands are
    # thread-safe, and phase A has already written the full raw graph. ──
    def _extract_ctx(ctx: dict) -> dict:
        si = ctx["si"]
        turns = [{"role": str(t.get("role") or "unknown"),
                  "content": str(t.get("content") or "")}
                 for t in ctx["session"]]
        worker_model = model_factory() if model_factory else model
        try:
            return {"si": si,
                    "out": extract_session_v2(
                        worker_model, turns, sdk=sdk,
                        session_id=ctx["s_node"],
                        session_date=ctx["session_date"] or None)}
        except Exception as ex:  # noqa: BLE001, RUF100
            return {"si": si, "exc": ex}

    if session_workers > 1 and len(ctxs) > 1:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=session_workers) as _ex:
            results = list(_ex.map(_extract_ctx, ctxs))
    else:
        results = [_extract_ctx(ctx) for ctx in ctxs]

    # ── Phase C (sequential): payload writes + consolidation records + the
    # extracted-point CONTAINS edges. All graph writes stay sequential. ──
    for ctx, res in zip(ctxs, results):  # noqa: B905
        si, s_node, session = ctx["si"], ctx["s_node"], ctx["session"]
        sid, session_date = ctx["sid"], ctx["session_date"]
        evidence_turns = ctx["evidence_turns"]
        turns = [{"role": str(t.get("role") or "unknown"),
                  "content": str(t.get("content") or "")}
                 for t in session]
        if "exc" in res:
            ex = res["exc"]
            stats["errors"].append(f"s{si}: {type(ex).__name__}: {ex}")  # kill the run
            # M4 (D4): the session-level exception is CLASSIFIED into the same
            # granular census vocabulary (S1/S2/S4 failures already ride in
            # out["error_census"]).
            _class = _classify_error(ex)
            stats["error_census"][_class] = stats["error_census"].get(_class, 0) + 1
            continue
        out = res["out"]
        payload = out.get("payload") or {}
        stats["turns"] += len(session)
        stats["minted_kinds"] += len(out.get("minted_kinds", []) or [])
        stats["supersessions"] += len(out.get("supersessions", []) or [])
        stats["errors"].extend(out.get("errors", []) or [])
        for _class, count in (out.get("error_census") or {}).items():
            stats["error_census"][_class] = stats["error_census"].get(_class, 0) + count

        # the ACTUAL writes (the _write_payload stats are authoritative —
        # they skip duplicates, so payload-len double-counts)
        written = _write_payload(sdk, payload, sid=sid, qid=qid, si=si,
                                 evidence_turns=all_evidence_turns,
                                 turns=turns, ev_sessions=ev_sessions,
                                 session_date=session_date or None,
                                 gold_answer=gold_answer,
                                 n_turns=len(session))
        for k in ("points", "events", "entities", "operators",
                  "evidence_points"):
            stats[k] += written.get(k, 0)
        stats["supersessions_written"] += written.get("supersessions_written", 0)
        for mk in ("source_session", "verbatim", "raw_chunk",
                   "answer_string"):
            stats["evidence_marks"][mk] = (
                stats["evidence_marks"].get(mk, 0)
                + written.get("evidence_marks", {}).get(mk, 0))

        # E7 (D4/D5): apply the result-level consolidation records — NOOP
        # folds (duplicates stamp + CONTAINS link + has_answer OR-in) and
        # DELETE-soft retractions (retract_point tombstone). Both stay
        # OUT of the Layer-1 payload (D8) — they ride the extractor result.
        stats["noops_applied"] += _apply_noops(
            sdk, out.get("noops") or [], s_node=s_node,
            has_evidence=bool(evidence_turns))
        stats["deletions_applied"] += _apply_deletions(
            sdk, out.get("deletions") or [])

        # ── Session CONTAINS the extracted points ──
        for p in payload.get("points", []) or []:
            pid = str(p.get("id", "")).strip()
            if not pid:
                continue
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": pid},
            )

    return stats


def _apply_noops(sdk: TortoiseSDK, noops: list[dict], *, s_node: str,
                 has_evidence: bool) -> int:
    """D4 write path: apply result-level NOOP records to the eval graph.

    For each record — the folded session's ref is stamped onto the CANONICAL
    point (additive ``duplicates`` list property, set-merge — idempotent:
    a re-run appends nothing new), the Session gets the link-only CONTAINS
    edge (existing edge types only — NO new edge type, NO IMPL), and when
    the folded session carried an answer turn ``has_answer`` is OR'd onto
    the canonical point (evidence-marking OR-in, mirrors the #1369 P2
    collision OR-in). Physically ONE point → retrieval dedup by
    construction (E2E-11 no-double-count). Best-effort: any failure is
    warned and skipped. Returns the count applied."""
    proj = sdk._get_proj()
    applied = 0
    for rec in noops or []:
        pid = str(rec.get("point_id") or "").strip()
        ref = str(rec.get("session_ref") or s_node or "").strip()
        if not pid:
            continue
        try:
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) "
                "SET p.duplicates = coalesce(p.duplicates, []) + "
                "    CASE WHEN $ref IN coalesce(p.duplicates, []) "
                "         THEN [] ELSE [$ref] END "
                "RETURN p.id",
                params={"id": pid, "ref": ref}).result_set
            if not rows:
                logger.warning("v2 noop target %s missing — skipped", pid)
                continue
            if has_evidence:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": pid})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
                "MERGE (s)-[:CONTAINS]->(p)",
                params={"sid": s_node, "pid": pid})
            applied += 1
        except Exception as ex:  # noqa: BLE001, RUF100 — best-effort in the eval
            logger.warning("v2 noop stamp on %s failed: %s", pid, ex)
    return applied


def _apply_deletions(sdk: TortoiseSDK, deletions: list[dict]) -> int:
    """D5 write path: apply result-level DELETE-soft records via the
    EXISTING canonical ``retract_point`` — status='retracted' tombstone
    (point stays in the graph; no resurrect on recall by construction —
    default retrieval excludes terminal statuses, #1391). Best-effort:
    a missing/already-terminal point raises ValueError → warned, the eval
    never dies on a delete. Returns the count applied."""
    applied = 0
    for rec in deletions or []:
        pid = str(rec.get("point_id") or "").strip()
        if not pid:
            continue
        try:
            sdk.retract_point(pid)
            applied += 1
        except ValueError as e:
            logger.warning("v2 deletion %s skipped (fail-open): %s", pid, e)
    return applied


def ingest_haystack_v2(sdk: TortoiseSDK, question: dict,  # noqa: F811
                       model: Any, *, chunk_turns: int = 2,
                       session_workers: int = 1,
                       model_factory: Callable | None = None) -> dict:
    """v2 ingest: each haystack session through extract_session_v2 → the
    payload written to the eval graph (Session + turn-granular raw chunks
    retained — the verbatim recall mitigation). Returns stats for
    provenance (mirrors ingest_haystack's shape). ``chunk_turns`` (R1
    #1540) is the turns-per-window granularity of the raw chunks (>= 1).

    ``session_workers`` (pilot #1549 — session-parallelism): when > 1, the
    LLM extraction (the wall-clock dominant phase) runs across the sessions
    of THIS question in parallel (the DeepSeek API sustains ~11 calls/s at
    16 concurrent — measured). Graph writes stay sequential (thread-safe by
    construction): phase A writes the session nodes + turn/chunk raw leg for
    ALL sessions, phase B extracts in parallel (each worker uses its own
    ``model_factory()`` model so the RoutingModel's mutable route/truncation
    state is never shared across threads), phase C writes each payload +
    consolidation records. ``model_factory`` (callable → fresh model) is
    REQUIRED for session_workers > 1; otherwise the shared model is used.
    """
    from tortoise.extractor_v2 import _classify_error, extract_session_v2

    qid = question["question_id"]
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    dates: list[str] = question.get("haystack_dates") or []
    ids: list[str] = question.get("haystack_session_ids") or []
    stats = {"sessions": 0, "turns": 0, "chunks": 0, "points": 0,
             "events": 0, "entities": 0, "operators": 0, "evidence_points": 0,
             "evidence_turns": 0, "minted_kinds": 0, "supersessions": 0,
             "supersessions_written": 0,
             "noops_applied": 0, "deletions_applied": 0,
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0, "answer_string": 0}, "errors": [],
             # M4 (#1524, D4): the per-question error census — rolled up from
             # each session's extractor ``error_census`` + the session-level
             # exception class; feeds outcome ``valid``/``error_classes``.
             "error_census": {},
             # #1746 (D7): the per-question LLM telemetry + recovery counters
             # rolled from each session's extractor result — feeds the
             # report's warning-only truncation readout (criterion 3: no
             # UNRECORDED truncation with valid=true).
             "llm": {"calls": 0, "retries": 0, "truncated": 0},
             "recovery": {}}
    # M6: the evidence-session id set (haystack sessions containing >=1
    # has_answer turn) + ALL answer-turn contents (question-wide — marks
    # (b)/(c) match against every answer turn, wherever it lives).
    ev_sessions = evidence_sessions(question)
    all_evidence_turns = [
        str(t.get("content") or "")
        for session in sessions for t in session if t.get("has_answer")]
    # #1763: the GOLD ANSWER STRING (mark (d)) — a benchmark truth the eval
    # harness holds via the question dict (the extractor LLM never sees it),
    # so answer-string marks are computed at eval-ingest time, never at
    # extraction.
    gold_answer = str(question.get("answer") or "")

    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"
        evidence_turns = [str(t.get("content") or "") for t in session
                          if t.get("has_answer")]
        stats["evidence_turns"] += len(evidence_turns)
        # R5 (#1544): points in a dated session carry the session date as
        # their creation time; undated sessions get the explicit sentinel.
        point_created_at = session_date or UNDATED_SENTINEL

        # ── Session node (mirrors the deterministic leg) ──
        sdk._get_proj().g.query(
            "MERGE (s:Session {id:$id}) "
            "SET s.created_at=coalesce(s.created_at, $ts), "
            "    s.turn_count=$tc, s.is_episodic=true, s.lme_question_id=$qid, "
            "    s.lme_session_index=$si, s.lme_source_session_id=$sid",
            params={"id": s_node, "ts": session_date or _now_iso(), "tc": len(session),
                    "qid": qid, "si": si, "sid": sid},
        )
        stats["sessions"] += 1
        proj = sdk._get_proj()

        # E7 (#1539 D6): ONE batch existence probe per session (turn ids +
        # raw chunk ids) — the per-turn/per-chunk ``_point_exists`` N+1
        # collapses to O(1) queries per session at 500-Q run scale.
        turn_ids = [f"lme:{qid}:s{si}:t{ti}" for ti in range(len(session))]
        session_chunks = list(_session_chunks(session, chunk_turns))
        chunk_ids = [f"lme:{qid}:s{si}:c{ci}" for ci, _, _ in session_chunks]
        existing = _existing_point_ids(proj, turn_ids + chunk_ids)

        # ── E3 (D8): turn points — the speaker-derivation substrate. Same
        # deterministic ids + speaker property as the v1 leg; has_answer is
        # NOT set (v2 turn/evidence recall measures extracted points). ──
        for ti, turn in enumerate(session):
            role = str(turn.get("role") or "unknown")
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            if turn_id not in existing:
                sdk.create_point(
                    "event", f"[{role}] {turn.get('content') or ''!s}",
                    id=turn_id, session_id=sid, lme_question_id=qid,
                    lme_session_index=si, speaker=role,
                    is_episodic=True, status="draft",
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": turn_id})

        # ── Raw verbatim turn-granular chunks (R1 #1540) + containment
        # marks (M6, mark c). Written BEFORE extraction so verbatim
        # retention + marks survive an extractor failure on this session
        # (fail-closed — the raw evidence leg is never silently lost). ──
        # v2 chunks ARE marked (D5): a chunk is an evidence chunk iff any
        # contained turn is an evidence turn (the union of a session's chunks
        # is the session, so no evidence turn is orphaned).
        for ci, text, turn_idxs in session_chunks:
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            contains_evidence = any(
                bool(turn.get("has_answer"))
                for ti, turn in enumerate(session) if ti in turn_idxs)
            if chunk_id not in existing:
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    has_answer=contains_evidence, status="draft",
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
                stats["chunks"] += 1  # written (post-guard) — stats == graph
            elif contains_evidence:
                # Idempotent OR-in: never overwrite a True with False.
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": chunk_id})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": chunk_id},
            )

        # ── The v2 extraction (production pipeline, embedded-safe S3) ──
        turns = [{"role": str(t.get("role") or "unknown"),
                  "content": str(t.get("content") or "")}
                 for t in session]
        try:
            out = extract_session_v2(model, turns, sdk=sdk,
                                     session_id=s_node,
                                     session_date=session_date or None)
        except Exception as ex:  # noqa: BLE001, RUF100
            stats["errors"].append(f"s{si}: {type(ex).__name__}: {ex}")  # kill the run
            # M4 (D4): the session-level exception is CLASSIFIED into the same
            # granular census vocabulary (S1/S2/S4 failures already ride in
            # out["error_census"]).
            _class = _classify_error(ex)
            stats["error_census"][_class] = stats["error_census"].get(_class, 0) + 1
            # #1746 (D7): the session-level exception path contributes one
            # call / zero truncations to the llm roll-up.
            stats["llm"]["calls"] += 1
            continue
        payload = out.get("payload") or {}
        stats["turns"] += len(session)
        stats["minted_kinds"] += len(out.get("minted_kinds", []) or [])
        stats["supersessions"] += len(out.get("supersessions", []) or [])
        stats["errors"].extend(out.get("errors", []) or [])
        for _class, count in (out.get("error_census") or {}).items():
            stats["error_census"][_class] = stats["error_census"].get(_class, 0) + count
        # #1746 (D7): thread the extractor's llm telemetry + recovery counters
        # through the ingest stats — the report's warning-only truncation
        # readout + recovery observability.
        _llm = (out.get("stats") or {}).get("llm") or {}
        for _k in ("calls", "retries", "truncated"):
            stats["llm"][_k] += _llm.get(_k, 0)
        for _k, _v in ((out.get("stats") or {}).get("recovery") or {}).items():
            stats["recovery"][_k] = stats["recovery"].get(_k, 0) + _v

        # the ACTUAL writes (the _write_payload stats are authoritative —
        # they skip duplicates, so payload-len double-counts)
        written = _write_payload(sdk, payload, sid=sid, qid=qid, si=si,
                                 evidence_turns=all_evidence_turns,
                                 turns=turns, ev_sessions=ev_sessions,
                                 session_date=session_date or None,
                                 gold_answer=gold_answer,
                                 n_turns=len(session))
        for k in ("points", "events", "entities", "operators",
                  "evidence_points"):
            stats[k] += written.get(k, 0)
        stats["supersessions_written"] += written.get("supersessions_written", 0)
        for mk in ("source_session", "verbatim", "raw_chunk",
                   "answer_string"):
            stats["evidence_marks"][mk] = (
                stats["evidence_marks"].get(mk, 0)
                + written.get("evidence_marks", {}).get(mk, 0))

        # E7 (D4/D5): apply the result-level consolidation records — NOOP
        # folds (duplicates stamp + CONTAINS link + has_answer OR-in) and
        # DELETE-soft retractions (retract_point tombstone). Both stay
        # OUT of the Layer-1 payload (D8) — they ride the extractor result.
        stats["noops_applied"] += _apply_noops(
            sdk, out.get("noops") or [], s_node=s_node,
            has_evidence=bool(evidence_turns))
        stats["deletions_applied"] += _apply_deletions(
            sdk, out.get("deletions") or [])

        # ── Session CONTAINS the extracted points ──
        for p in payload.get("points", []) or []:
            pid = str(p.get("id", "")).strip()
            if not pid:
                continue
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": pid},
            )
    return stats


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017
