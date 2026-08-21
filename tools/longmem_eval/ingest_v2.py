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
from .ingest import SESSION_TRANSCRIPT_KIND, _point_exists, _session_transcript  # noqa: E402


def _write_payload(sdk: TortoiseSDK, payload: dict, *, sid: str, qid: str,
                   si: int, evidence_turns: list[str], turns: list[dict],
                   ev_sessions: set[str]) -> dict:
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
            sdk.create_point(
                "statement", content, id=pid, session_id=sid,
                lme_question_id=qid, lme_session_index=si,
                is_episodic=True, has_answer=mark["has_answer"],
                quote=quote, status="draft",
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
            sdk.create_event(
                content[:80], str(ev.get("eventKind", "core:occurrence"))
                .rsplit(":", 1)[-1],
                sessionId=sid, lme_question_id=qid, lme_session_index=si,
                is_episodic=True,
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

    stats["minted_kinds"] = len(payload.get("minted_kinds", []) or [])
    return stats


def ingest_haystack_v2(sdk: TortoiseSDK, question: dict,
                       model: Any) -> dict:
    """v2 ingest: each haystack session through extract_session_v2 → the
    payload written to the eval graph (Session + raw transcript retained).
    Returns stats for provenance (mirrors ingest_haystack's shape)."""
    from tortoise.extractor_v2 import extract_session_v2

    qid = question["question_id"]
    sessions: list[list[dict]] = question.get("haystack_sessions") or []
    dates: list[str] = question.get("haystack_dates") or []
    ids: list[str] = question.get("haystack_session_ids") or []
    stats = {"sessions": 0, "turns": 0, "raw_transcripts": 0, "points": 0,
             "events": 0, "entities": 0, "operators": 0, "evidence_points": 0,
             "evidence_turns": 0, "minted_kinds": 0, "supersessions": 0,
             "evidence_marks": {"source_session": 0, "verbatim": 0,
                                "raw_chunk": 0}, "errors": []}
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

        # ── Raw verbatim transcript (retained — verbatim recall mitigation) ──
        # M6 mark (c)+(a): the answer-session transcript contains the answer
        # turns verbatim (52/52 on the healthy fixture) — has_answer=true so
        # the v2 leg is non-vacuous even when the extractor paraphrases
        # everything (the 402-run would have had marks via (c) alone).
        raw_id = f"lme:{qid}:s{si}:raw"
        raw_text = _session_transcript(session)
        raw_mark = mark_for({"content": raw_text, "quote": ""},
                            session_id=sid, evidence_sessions=ev_sessions,
                            answer_turn_contents=all_evidence_turns)
        if not _point_exists(sdk._get_proj(), raw_id):
            sdk.create_point(
                SESSION_TRANSCRIPT_KIND, raw_text,
                id=raw_id, session_id=sid, lme_question_id=qid,
                lme_session_index=si, is_episodic=True,
                has_answer=raw_mark["has_answer"], status="draft",
            )
            # create-only (consistent with the extracted-points accounting) —
            # re-ingest is a no-op, not a recount.
            if raw_mark["has_answer"]:
                stats["evidence_points"] += 1
            if raw_mark["marks"]["raw_chunk"]:
                stats["evidence_marks"]["raw_chunk"] += 1
        elif raw_mark["has_answer"]:
            # Idempotent OR-in: a raw transcript written by a pre-M6 run has
            # no has_answer prop — never overwrite a True with False.
            sdk._get_proj().g.query(
                "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                params={"id": raw_id})
        stats["raw_transcripts"] += 1
        sdk._get_proj().g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": s_node, "tid": raw_id},
        )

        # ── The v2 extraction (production pipeline, embedded-safe S3) ──
        turns = [{"role": str(t.get("role") or "unknown"),
                  "content": str(t.get("content") or "")}
                 for t in session]
        try:
            out = extract_session_v2(model, turns, sdk=sdk,
                                     session_id=s_node)
        except Exception as ex:  # noqa: BLE001, RUF100
            stats["errors"].append(f"s{si}: {type(ex).__name__}: {ex}")  # kill the run
            continue
        payload = out.get("payload") or {}
        stats["turns"] += len(session)
        stats["minted_kinds"] += len(out.get("minted_kinds", []) or [])
        stats["supersessions"] += len(out.get("supersessions", []) or [])
        stats["errors"].extend(out.get("errors", []) or [])

        # the ACTUAL writes (the _write_payload stats are authoritative —
        # they skip duplicates, so payload-len double-counts)
        written = _write_payload(sdk, payload, sid=sid, qid=qid, si=si,
                                 evidence_turns=all_evidence_turns,
                                 turns=turns, ev_sessions=ev_sessions)
        for k in ("points", "events", "entities", "operators",
                  "evidence_points"):
            stats[k] += written.get(k, 0)
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
