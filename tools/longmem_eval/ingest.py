"""Haystack → Tortoise graph ingestion adapter (issue #1144, axis 2).

For each LongMemEval question, its ``haystack_sessions`` (the user/assistant
chat history) are ingested into a FRESH graph so retrieval measures per-
question recall the way the benchmark intends (each question's history is an
independent memory; no cross-question contamination — the design-locked axis 2
protocol "ingest transcripts → hybrid retrieval from graph + raw sessions").

Per session we write (all deterministic — no LLM keys required):
  * a ``:Session`` node (id ``lme:{qid}:s{si}``) with the session timestamp,
  * one episodic turn Point per turn (id ``lme:{qid}:s{si}:t{i}``,
    pointKind ``event``, content ``[role] text`` — mirrors
    ``TortoiseSDK.capture_session``'s episodic-turn shape, plus a
    ``has_answer`` flag on evidence turns for turn-level recall),
  * one raw-transcript Point per session (pointKind ``session-transcript``,
    content = the FULL verbatim session text — indexed raw alongside the
    graph points because competitor RAGs win on verbatim single-session
    recall; the #1144 research says mitigate by indexing raw text too),
  * ``CONTAINS`` edges Session → turns and Session → raw transcript,
  * a provenance ``:Event`` (kind ``lmeHaystackCaptured``) linking the
    question's sessions (best-effort, mirrors capture_session's non-fatal
    event write).

The "extraction approach" (deterministic episodic + raw transcript points in
v1; LLM epistemic extraction is a documented future option) is recorded in
the report provenance so published numbers carry their methodology.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from tortoise.domain_loader import register_kind
from tortoise.sdk import TortoiseSDK

from .evidence import evidence_sessions, mark_for

logger = logging.getLogger(__name__)

# Raw-session transcript points live under this pointKind (open-ended
# vocabulary — registration suppresses the SDK warning, mirroring the sdk's
# own register_kind("diary") pattern). "event" is the episodic turn-point
# kind used by TortoiseSDK.capture_session (registered here so the pack
# vocabulary doesn't warn on every turn write).
SESSION_TRANSCRIPT_KIND = "session-transcript"
register_kind(SESSION_TRANSCRIPT_KIND)
register_kind("event")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _session_transcript(session: list[dict]) -> str:
    """Render a session's turns as verbatim text (role-prefixed lines)."""
    lines: list[str] = []
    for turn in session:
        role = str(turn.get("role") or "unknown")
        content = str(turn.get("content") or "")
        lines.append(f"{role.title()}: {content}")
    return "\n".join(lines)


def _point_exists(proj, pid: str) -> bool:
    """True when a Point with this deterministic id already exists.

    ``create_point`` uses CREATE (not MERGE), so re-running ingest over the
    same fresh graph would duplicate points — the idempotency contract of
    the runner (re-run is a no-op). Session/CONTAINS writes already MERGE.
    """
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN 1 LIMIT 1", params={"id": pid}).result_set
    return bool(rows)


def question_node_ids(question: dict) -> dict[str, str]:
    """Deterministic node-id prefixes for a question (fresh graph per qid)."""
    qid = question["question_id"]
    return {"session": f"lme:{qid}:s", "turn": f"lme:{qid}:s", "raw": f"lme:{qid}:s"}


def ingest_haystack(sdk: TortoiseSDK, question: dict) -> dict:
    """Ingest one question's haystack sessions into ``sdk``'s graph.

    Returns a stats dict (sessions, turns, raw_transcripts) for provenance.
    Idempotent per node (MERGE / explicit deterministic ids) so a re-run over
    the same fresh graph cannot double-write.
    """
    qid = question["question_id"]
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    dates: list[str] = question.get("haystack_dates") or []
    ids: list[str] = question.get("haystack_session_ids") or []
    evidence_turns = 0
    evidence_points = 0
    raw_transcripts = 0
    # M6: the evidence-session id set + answer-turn contents (shared with
    # the v2 leg via evidence.py) — the raw transcript mark (c)/(a).
    ev_sessions = evidence_sessions(question)
    evidence_turn_contents: list[str] = []
    for session in sessions:
        evidence_turn_contents.extend(
            str(t.get("content") or "") for t in session if t.get("has_answer"))

    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"

        # ── Session node ──
        sdk._get_proj().g.query(
            "MERGE (s:Session {id:$id}) "
            "SET s.created_at=coalesce(s.created_at, $ts), "
            "    s.turn_count=$tc, s.is_episodic=true, s.lme_question_id=$qid, "
            "    s.lme_session_index=$si, s.lme_source_session_id=$sid",
            params={"id": s_node, "ts": session_date or _now_iso(), "tc": len(session),
                    "qid": qid, "si": si, "sid": sid},
        )

        for ti, turn in enumerate(sessions[si]):
            role = turn.get("role") or "unknown"
            content = str(turn.get("content") or "")
            is_evidence = bool(turn.get("has_answer"))
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            turn_text = f"[{role}] {content}"

            # Episodic turn point — create_point computes content-hash +
            # embedding (gracefully None when no embedder), accepts explicit
            # deterministic id (mirrors capture_session's turn shape).
            if not _point_exists(sdk._get_proj(), turn_id):
                sdk.create_point(
                    "event",
                    turn_text,
                    id=turn_id,
                    session_id=sid,
                    lme_question_id=qid,
                    lme_session_index=si,
                    speaker=str(role),
                    is_episodic=True,
                    has_answer=is_evidence,
                    status="draft",
                )
                # M6: evidence_points counts points WRITTEN this run
                # (create-only, mirroring the v2 leg) — re-ingest is a no-op,
                # not a recount; evidence_turns stays the dataset-derived
                # denominator below.
                if is_evidence:
                    evidence_points += 1
            if is_evidence:
                evidence_turns += 1
            sdk._get_proj().g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": turn_id},
            )

        # ── Raw verbatim transcript point (the "index raw text too" leg) ──
        # M6 (#1526): the raw transcript carries the evidence marks like the
        # v2 leg — mark (c) raw-chunk containment (the answer session's
        # transcript contains the answer turns verbatim) and mark (a)
        # source-session attribution (its session_id is an evidence session).
        # Both legs then produce the same evidence_points accounting (D7) so
        # evidence_coverage is comparable across ingest modes; evidence-turn
        # points themselves are unchanged (their own has_answer flag).
        raw_id = f"lme:{qid}:s{si}:raw"
        raw_text = _session_transcript(session)
        raw_mark = mark_for({"content": raw_text, "quote": ""},
                            session_id=sid, evidence_sessions=ev_sessions,
                            answer_turn_contents=evidence_turn_contents)
        if not _point_exists(sdk._get_proj(), raw_id):
            sdk.create_point(
                SESSION_TRANSCRIPT_KIND,
                raw_text,
                id=raw_id,
                session_id=sid,
                lme_question_id=qid,
                lme_session_index=si,
                is_episodic=True,
                has_answer=raw_mark["has_answer"],
                status="draft",
            )
            if raw_mark["has_answer"]:
                evidence_points += 1  # create-only (v2-leg mirror)
        elif raw_mark["has_answer"]:
            # Idempotent OR-in: a raw transcript written by a pre-M6 run has
            # no has_answer prop — never overwrite a True with False.
            sdk._get_proj().g.query(
                "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                params={"id": raw_id})
        raw_transcripts += 1
        sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": s_node, "tid": raw_id},
        )

    # ── Provenance event (best-effort — mirrors capture_session) ──
    try:
        ev = sdk.create_event(
            f"lme_{qid}", "lmeHaystackCaptured",
            startedAt=_now_iso(), endedAt=_now_iso(),
            sessionId=qid, lme_question_id=qid, is_episodic=True,
        )
        event_id = ev.get("id") or ev.get("eventId")
        if event_id:
            proj = sdk._get_proj()
            for si in range(len(sessions)):
                proj.g.query(
                    "MATCH (e:Event {id:$eid}), (s:Session {id:$sid}) "
                    "MERGE (e)-[:aboutSession]->(s)",
                    params={"eid": event_id, "sid": f"lme:{qid}:s{si}"},
                )
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("lmeHaystackCaptured event write failed (non-fatal): %s", e)

    return {
        "question_id": qid,
        "sessions": len(sessions),
        "turns": sum(len(s) for s in sessions),
        "evidence_turns": evidence_turns,
        "raw_transcripts": raw_transcripts,
        "evidence_points": evidence_points,
    }


def point_props_for_hits(proj, point_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch (session_id, has_answer, lme_session_index) for a list of Point
    ids in one Cypher query (avoid N+1 on the retrieval path)."""
    if not point_ids:
        return {}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.session_id, ''), coalesce(n.has_answer, false), "
        "       coalesce(n.lme_session_index, -1)",
        params={"ids": point_ids},
    ).result_set
    return {row[0]: {"session_id": row[1], "has_answer": bool(row[2]),
                     "lme_session_index": row[3]} for row in rows}
