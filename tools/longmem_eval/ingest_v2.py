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
from typing import Any

from tortoise.sdk import TortoiseSDK

logger = logging.getLogger(__name__)

from .evidence import (EVIDENCE_QUOTE_CAP, anchor_quote, evidence_sessions,  # noqa: E402, I001
                       mark_for)
from .evidence import _overlap  # noqa: F401, E402 — back-compat re-export
from .ingest import (SESSION_TRANSCRIPT_KIND, _point_exists, _session_chunks)  # noqa: E402


def _point_status(proj, pid: str) -> str:
    """The point's persisted status — '' when the point does not exist."""
    rows = proj.g.query(
        "MATCH (p:Point {id:$id}) RETURN p.status",
        params={"id": pid}).result_set
    return str(rows[0][0] or "") if rows else ""


def _write_payload(sdk: TortoiseSDK, payload: dict, *, sid: str, qid: str,
                   si: int, evidence_turns: list[str],
                   session_date: str | None = None,
                   turns: list[dict], ev_sessions: set[str],
                   n_turns: int = 0) -> dict:
    """Write a v2 Layer-1 payload into the eval graph. Idempotent per point
    (explicit deterministic ids + _point_exists guard). Returns stats.

    M6 evidence marking: each extracted point gets the OR of the three marks
    (source-session attribution / verbatim quote anchor / raw-chunk
    containment) and a D3-deterministically-anchored quote (the extractor
    emits an empty quote; gate 2: consume a non-empty payload quote
    instead). The per-mark breakdown is counted in
    ``stats["evidence_marks"]`` so the report can say WHY evidence exists."""
    stats = {"entities": 0, "points": 0, "events": 0, "operators": 0,
             "evidence_points": 0, "minted_kinds": 0,
             "supersessions_written": 0,
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0}}
    proj = sdk._get_proj()

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
                        answer_turn_contents=evidence_turns)
        if _point_exists(proj, pid):
            # #1369 review P2: content-addressed collision across sessions —
            # OR-in this session's evidence marking (M6: never overwrite a
            # True with False on collision; first-writer props keep the
            # session id; the raw-transcript leg mitigates attribution).
            if mark["has_answer"]:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
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
            if p.get("when"):
                point_props["when"] = str(p.get("when"))
            sdk.create_point(
                "statement", content, id=pid, session_id=sid,
                lme_question_id=qid, lme_session_index=si,
                is_episodic=True, has_answer=mark["has_answer"],
                quote=quote, status="draft",
                search_keys=p.get("search_keys") or None,
                source_turn_id=turn_ref,
                reason=str(p.get("reason") or "NEW"),   # E5: REVISES visible on the node
                **point_props,
            )
            stats["points"] += 1
            if mark["has_answer"]:
                stats["evidence_points"] += 1
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
        if not _point_exists(proj, src) or not _point_exists(proj, dst):
            continue
        # #1369 review P2: operator idempotency — create_operator mints a
        # fresh node each call; guard on the (op_type, src, dst) triple
        # (op_type is validated IMPL/NAND — safe to inline as the rel type).
        existing = proj.g.query(
            f"MATCH (o:Point {{is_operator:true, op_type:$t}})-[:{op_type} {{idx:0}}]->(s) "
            f"WHERE s.id = $src "
            f"MATCH (o)-[:{op_type} {{idx:1}}]->(d) WHERE d.id = $dst "
            "RETURN count(*) LIMIT 1",
            params={"t": op_type,
                    "src": src, "dst": dst}).result_set
        if existing and existing[0][0]:
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
    # supersede_point would raise on a terminal old). ──
    for sr in payload.get("supersessions", []) or []:
        old_id = str(sr.get("superseded", "") or "").strip()
        new_id = str(sr.get("supersedes_by", "") or "").strip()
        if not (old_id.startswith("pt_") and new_id.startswith("pt_")) \
                or old_id == new_id:
            continue
        if not _point_exists(proj, old_id) or not _point_exists(proj, new_id):
            logger.warning("v2 supersession skip %s→%s: endpoint missing "
                           "(fail-open)", old_id, new_id)
            continue
        if _point_status(proj, old_id) in ("superseded", "retracted",
                                           "archived"):
            continue   # idempotent re-ingest — already terminal
        try:
            sdk.supersede(old_id, new_id)     # EXISTING canonical unified tool
            stats["supersessions_written"] += 1
        except Exception as ex:  # noqa: BLE001, RUF100 — best-effort in the eval
            logger.warning("v2 supersede %s→%s failed: %s", old_id, new_id, ex)

    stats["minted_kinds"] = len(payload.get("minted_kinds", []) or [])
    return stats


def ingest_haystack_v2(sdk: TortoiseSDK, question: dict,
                       model: Any, *, chunk_turns: int = 2) -> dict:
    """v2 ingest: each haystack session through extract_session_v2 → the
    payload written to the eval graph (Session + turn-granular raw chunks
    retained — the verbatim recall mitigation). Returns stats for
    provenance (mirrors ingest_haystack's shape). ``chunk_turns`` (R1
    #1540) is the turns-per-window granularity of the raw chunks (>= 1).
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
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0}, "errors": [],
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

    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"
        evidence_turns = [str(t.get("content") or "") for t in session
                          if t.get("has_answer")]
        stats["evidence_turns"] += len(evidence_turns)

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

        # ── E3 (D8): turn points — the speaker-derivation substrate. Same
        # deterministic ids + speaker property as the v1 leg; has_answer is
        # NOT set (v2 turn/evidence recall measures extracted points). ──
        for ti, turn in enumerate(session):
            role = str(turn.get("role") or "unknown")
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            if not _point_exists(sdk._get_proj(), turn_id):
                sdk.create_point(
                    "event", f"[{role}] {turn.get('content') or ''!s}",
                    id=turn_id, session_id=sid, lme_question_id=qid,
                    lme_session_index=si, speaker=role,
                    is_episodic=True, status="draft",
                )
            sdk._get_proj().g.query(
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
        for ci, text, turn_idxs in _session_chunks(session, chunk_turns):
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            contains_evidence = any(
                bool(turn.get("has_answer"))
                for ti, turn in enumerate(session) if ti in turn_idxs)
            if not _point_exists(sdk._get_proj(), chunk_id):
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    has_answer=contains_evidence, status="draft",
                )
                stats["chunks"] += 1  # written (post-guard) — stats == graph
            elif contains_evidence:
                # Idempotent OR-in: never overwrite a True with False.
                sdk._get_proj().g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": chunk_id})
            sdk._get_proj().g.query(
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
            continue
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
                                 n_turns=len(session))
        for k in ("points", "events", "entities", "operators",
                  "evidence_points"):
            stats[k] += written.get(k, 0)
        stats["supersessions_written"] += written.get("supersessions_written", 0)
        for mk in ("source_session", "verbatim", "raw_chunk"):
            stats["evidence_marks"][mk] = (
                stats["evidence_marks"].get(mk, 0)
                + written.get("evidence_marks", {}).get(mk, 0))

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
