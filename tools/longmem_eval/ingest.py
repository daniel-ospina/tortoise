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
  * turn-granular raw chunk Points (R1 #1540): pointKind
    ``session-transcript``, ids ``lme:{qid}:s{si}:c{ci}`` — non-overlapping
    verbatim windows of ``chunk_turns`` turns each, rendered with the same
    role-prefixed verbatim format. The union of a session's chunks == the
    full verbatim session text (the owner invariant: extraction never
    replaces verbatim evidence; the whole-session ``:raw`` blob — the
    measured 4.4x context bloat — is retired). Chunks are written
    UNMARKED in deterministic mode (D3 #1540: the deterministic leg keeps
    its turn-id evidence path), and are indexed raw alongside the graph
    points because competitor RAGs win on verbatim single-session recall,
  * ``CONTAINS`` edges Session → turns and Session → chunks,
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

logger = logging.getLogger(__name__)

#: R1 (#1540): turns per raw-chunk window (the granularity knob; the run
#: protocol step-2 sweep selects the value for the pilot + 500-Q run).
DEFAULT_CHUNK_TURNS = 2

# Raw-session transcript points live under this pointKind (open-ended
# vocabulary — registration suppresses the SDK warning, mirroring the sdk's
# own register_kind("diary") pattern). "event" is the episodic turn-point
# kind used by TortoiseSDK.capture_session (registered here so the pack
# vocabulary doesn't warn on every turn write).
SESSION_TRANSCRIPT_KIND = "session-transcript"
register_kind(SESSION_TRANSCRIPT_KIND)
register_kind("event")

# R4 (#1543): the extracted-point kind written by the v2 ingest (must match
# the extractor's default pointKind — extractor_v2 falls back to
# "statement"). The structural retrieval leg scans THIS kind and hop-expands
# text hits over IMPL/NAND edges. Single source of truth so write/read can't
# drift (drift = silent empty structural leg).
EXTRACTION_POINT_KIND = "statement"

# R5 (#1544): the explicit sentinel for UNDATED sessions — deterministic-
# oldest so the recency factor is 0.0 (never the server default
# createdAt=now, which would silently give an undated session MAXIMUM
# recency boost). The reader never consumes createdAt (it renders the
# dataset's haystack_dates annotation), so the sentinel cannot cause a
# false date-answer (E2E-4 owned negative stays safe).
UNDATED_SENTINEL = "1970-01-01T00:00:00Z"

# R5 (#1544): the eval-instrumentation eventKind for one dated timeline
# :Event per haystack session (mirrors the existing lmeHaystackCaptured
# pattern — NOT an ontology kind; the timeline surface E2E-4 reads).
TIMELINE_EVENT_KIND = "lmeHaystackSession"


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


def _session_chunks(session: list[dict], chunk_turns: int) -> list[tuple[int, str, list[int]]]:
    """Non-overlapping verbatim turn windows of ``chunk_turns`` turns each.

    Returns ``[(chunk_index, rendered_text, contained_turn_indices)]`` — the
    union of rendered texts == the full session transcript (owner invariant:
    raw verbatim evidence is always retained; the whole-session ``:raw`` blob
    is retired, R1 #1540). ``chunk_turns`` must be >= 1 — 0 would crash
    ``range(step=0)`` mid-ingest and negative would silently produce zero
    chunks (the verbatim leg silently deleted)."""
    if chunk_turns < 1:
        raise ValueError(f"chunk_turns must be >= 1, got {chunk_turns!r}")
    windows: list[tuple[int, str, list[int]]] = []
    for start in range(0, len(session), chunk_turns):
        window = session[start:start + chunk_turns]
        windows.append((start // chunk_turns, _session_transcript(window),
                        list(range(start, start + len(window)))))
    return windows


def _point_exists(proj, pid: str) -> bool:
    """True when a Point with this deterministic id already exists.

    ``create_point`` uses CREATE (not MERGE), so re-running ingest over the
    same fresh graph would duplicate points — the idempotency contract of
    the runner (re-run is a no-op). Session/CONTAINS writes already MERGE.
    """
    rows = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN 1 LIMIT 1", params={"id": pid}).result_set
    return bool(rows)


def _existing_point_ids(proj, ids: list[str]) -> set[str]:
    """Batch existence probe (E7 #1539 D6 — surface 12 N+1 fix): ONE
    ``MATCH (n:Point) WHERE n.id IN $ids RETURN n.id`` query returns the
    set of ids that already exist. The per-turn/per-point ``_point_exists``
    N+1 at 500-Q run scale collapses to O(1) queries per session. Point-
    node semantics only — event/operator ids are simply absent from the
    result set (event-endpoint operator checks keep today's behavior)."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return set()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id",
        params={"ids": ids}).result_set
    return {row[0] for row in rows}


def question_node_ids(question: dict) -> dict[str, str]:
    """Deterministic node-id prefixes for a question (fresh graph per qid)."""
    qid = question["question_id"]
    return {"session": f"lme:{qid}:s", "turn": f"lme:{qid}:s", "raw": f"lme:{qid}:s"}


def ingest_haystack(sdk: TortoiseSDK, question: dict, *,
                    chunk_turns: int = DEFAULT_CHUNK_TURNS) -> dict:
    """Ingest one question's haystack sessions into ``sdk``'s graph.

    Returns a stats dict (sessions, turns, chunks) for provenance.
    Idempotent per node (MERGE / explicit deterministic ids) so a re-run over
    the same fresh graph cannot double-write. ``chunk_turns`` (R1 #1540) is
    the turns-per-window granularity of the raw verbatim chunks (>= 1).
    """
    qid = question["question_id"]
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    dates: list[str] = question.get("haystack_dates") or []
    ids: list[str] = question.get("haystack_session_ids") or []
    evidence_turns = 0
    evidence_points = 0
    chunks = 0

    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"
        proj = sdk._get_proj()
        # R5 (#1544): points in a dated session carry the session date as
        # their creation time; undated sessions get the explicit sentinel
        # (deterministic-oldest → recency factor 0.0, never the server
        # default createdAt=now which would max the boost).
        point_created_at = session_date or UNDATED_SENTINEL

        # ── Session node ──
        proj.g.query(
            "MERGE (s:Session {id:$id}) "
            "SET s.created_at=coalesce(s.created_at, $ts), "
            "    s.turn_count=$tc, s.is_episodic=true, s.lme_question_id=$qid, "
            "    s.lme_session_index=$si, s.lme_source_session_id=$sid",
            params={"id": s_node, "ts": session_date or _now_iso(), "tc": len(session),
                    "qid": qid, "si": si, "sid": sid},
        )

        # E7 (#1539 D6): ONE batch existence probe per session (turn + raw
        # chunk ids together) — the per-turn ``_point_exists`` N+1 collapses
        # to O(1) queries per session at 500-Q run scale. Idempotency
        # semantics unchanged (the batch is just the existence probe).
        turn_ids = [f"lme:{qid}:s{si}:t{ti}" for ti in range(len(session))]
        session_chunks = list(_session_chunks(session, chunk_turns))
        chunk_ids = [f"lme:{qid}:s{si}:c{ci}" for ci, _, _ in session_chunks]
        existing = _existing_point_ids(proj, turn_ids + chunk_ids)

        for ti, turn in enumerate(sessions[si]):
            role = turn.get("role") or "unknown"
            content = str(turn.get("content") or "")
            is_evidence = bool(turn.get("has_answer"))
            turn_id = f"lme:{qid}:s{si}:t{ti}"
            turn_text = f"[{role}] {content}"

            # Episodic turn point — create_point computes content-hash +
            # embedding (gracefully None when no embedder), accepts explicit
            # deterministic id (mirrors capture_session's turn shape).
            if turn_id not in existing:
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
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
                # M6: evidence_points counts points WRITTEN this run
                # (create-only, mirroring the v2 leg) — re-ingest is a no-op,
                # not a recount; evidence_turns stays the dataset-derived
                # denominator below.
                if is_evidence:
                    evidence_points += 1
            if is_evidence:
                evidence_turns += 1
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": turn_id},
            )

        # ── Raw verbatim turn-granular chunks (R1 #1540: replaces the
        # whole-session blob — the measured 4.4x context bloat; the union of
        # chunks == the full session, so verbatim coverage is preserved). ──
        # Deterministic-mode chunks stay UNMARKED (D3): the deterministic leg
        # keeps its turn-id evidence path (evidence_turn_ids) — marking them
        # would flip retrieval into the v2 evidence-marks branch and silently
        # change baseline turn-recall semantics.
        for ci, text, turn_idxs in session_chunks:
            chunk_id = f"lme:{qid}:s{si}:c{ci}"
            if chunk_id not in existing:
                sdk.create_point(
                    SESSION_TRANSCRIPT_KIND, text, id=chunk_id,
                    session_id=sid, lme_question_id=qid,
                    lme_session_index=si, lme_chunk_index=ci,
                    lme_chunk_turns=len(turn_idxs), is_episodic=True,
                    status="draft",
                    createdAt=point_created_at,  # R5: session date (sentinel)
                )
                chunks += 1  # written (post-guard) — stats == graph state
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": s_node, "tid": chunk_id},
            )

        # ── R5 (#1544): the dated timeline :Event for this session ──
        # One ``lmeHaystackSession`` event per dated session (``startedAt ==
        # session_date``, ``sessionId == dataset sid`` — session_recall@k
        # keys on h["session_id"], so events join the same sessions as
        # points) with an aboutSession edge. Skipped entirely when
        # ``session_date`` is empty (undated session → no timeline event;
        # honest absence — no false date-answer). Intentionally minimal
        # name — proves the timeline surface, doesn't fake extraction (the
        # v2 leg's payload events carry the real content).
        if session_date:
            try:
                ev = sdk.create_event(
                    f"lme_{qid}_s{si}", TIMELINE_EVENT_KIND,
                    startedAt=session_date, endedAt=session_date,
                    sessionId=sid, lme_question_id=qid, lme_session_index=si,
                    is_episodic=True,
                )
                event_id = ev.get("id") or ev.get("eventId")
                if event_id:
                    proj.g.query(
                        "MATCH (e:Event {id:$eid}), (s:Session {id:$sid}) "
                        "MERGE (e)-[:aboutSession]->(s)",
                        params={"eid": event_id, "sid": s_node})
            except Exception as e:  # noqa: BLE001, RUF100
                # best-effort, mirrors the provenance event's non-fatal write
                logger.warning("lmeHaystackSession event write failed "
                               "(non-fatal): %s", e)

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
        "chunks": chunks,
        "evidence_points": evidence_points,
    }


def point_props_for_hits(proj, point_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch (session_id, has_answer, lme_session_index, quote, search_keys,
    source_turn_id, speaker, point_kind) for a list of Point ids in one
    Cypher query (avoid N+1 on the retrieval path). E3 (#1535): the
    source-turn link + speaker prop ride along so read-time speaker
    derivation is query-able. R1 (#1540): ``point_kind`` lets retrieval
    distinguish raw chunks from extracted points (per-session chunk dedup +
    the D5 evidence denominator split both key on the kind)."""
    if not point_ids:
        return {}
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.id IN $ids "
        "RETURN n.id, coalesce(n.session_id, ''), coalesce(n.has_answer, false), "
        "       coalesce(n.lme_session_index, -1), "
        "       coalesce(n.quote, ''), coalesce(n.search_keys, []), "
        "       coalesce(n.source_turn_id, ''), coalesce(n.speaker, ''), "
        "       coalesce(n.pointKind, '')",
        params={"ids": point_ids},
    ).result_set
    return {row[0]: {"session_id": row[1], "has_answer": bool(row[2]),
                     "lme_session_index": row[3], "quote": row[4],
                     "search_keys": row[5], "source_turn_id": row[6],
                     "speaker": row[7], "point_kind": row[8]}
            for row in rows}


def event_props_for_hits(proj, event_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch (session_id, lme_session_index) for a list of Event ids in one
    Cypher query (R5 #1544, D8 — the event side of the TR union pool).
    ``has_answer`` is hardcoded False: evidence marking is point-level (M6
    scope), so event hits can never inflate the evidence/turn denominators.
    Session linkage uses the same ``sessionId == dataset sid`` contract as
    points, so ``session_recall@k`` measures the union pool correctly and
    ``session_date`` annotation resolves through the existing
    lme_session_index → haystack_dates mapping (identical for both labels).
    """
    if not event_ids:
        return {}
    rows = proj.g.query(
        "MATCH (n:Event) WHERE n.eventId IN $ids "
        "RETURN n.eventId, coalesce(n.sessionId, ''), "
        "       coalesce(n.lme_session_index, -1), "
        "       coalesce(n.eventKind, '')",
        params={"ids": event_ids},
    ).result_set
    return {row[0]: {"session_id": row[1],
                     "lme_session_index": row[2],
                     "has_answer": False,
                     "point_kind": row[3]}
            for row in rows}
