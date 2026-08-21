"""v2 ingest mode for the LongMemEval runner (issue #1369).

Each haystack session goes through the PRODUCTION v2 extractor
(``extractor_v2.extract_session_v2`` — the default BYOK path since #1385,
with supersession extraction since #1394), and the resulting Layer-1 payload
is written to the eval graph: entities, points, events, operators. The
deterministic leg's Session nodes + raw-transcript points are RETAINED
(#1369 indicator 3 — verbatim recall mitigation) so v2 mode is comparable to
the deterministic baseline on the same retrieval.

Evidence marking (the extractor's recall contribution): a v2 point whose
content overlaps an answer turn (``has_answer`` in the dataset, >=0.4 token
overlap) is written with ``has_answer=True`` — so the runner's turn-recall@k
measures 'did the extracted point CONTAINING the answer surface in top-k'.
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
import re
from typing import Any

from tortoise.sdk import TortoiseSDK

logger = logging.getLogger(__name__)

from .ingest import SESSION_TRANSCRIPT_KIND, _point_exists, _session_transcript  # noqa: E402, I001


_STOPWORDS = {"the", "a", "an", "is", "are", "was", "were", "to", "of",
               "and", "or", "in", "on", "at", "for", "with", "it", "its",
               "this", "that", "i", "you", "he", "she", "we", "they",
               "my", "your", "me", "him", "her", "us", "them", "do", "did",
               "does", "have", "has", "had", "be", "been", "not", "no",
               "yes", "ok", "okay", "so", "but", "if", "then", "there",
               "here", "what", "when", "why", "how", "just", "very", "really"}


def _tokens(text: str) -> set[str]:
    return {t for t in re.sub(r"[^a-z0-9 ]", " ", (text or "").lower()).split()
            if t not in _STOPWORDS and len(t) > 1}


def _overlap(a: str, b: str) -> float:
    """Answer-content coverage: the fraction of the answer turn's content
    tokens present in the candidate point (stopwords stripped). Directional
    — a point is evidence only when it substantially contains the answer's
    meaning, not when it merely shares filler words."""
    tb = _tokens(b)
    if not tb:
        return 0.0
    ta = _tokens(a)
    if not ta:
        return 0.0
    return len(ta & tb) / len(tb)


def _evidence_marked(content: str, evidence_turns: list[str],
                     threshold: float = 0.4) -> bool:
    """True when a v2 point contains >= threshold of an answer turn's content
    tokens (stopword-stripped, >=2 content tokens required)."""
    return any(_overlap(content, turn) >= threshold
               and len(_tokens(turn)) >= 2 for turn in evidence_turns)


def _write_payload(sdk: TortoiseSDK, payload: dict, *, sid: str, qid: str,
                   si: int, evidence_turns: list[str],
                   session_date: str | None = None) -> dict:
    """Write a v2 Layer-1 payload into the eval graph. Idempotent per point
    (explicit deterministic ids + _point_exists guard). Returns stats."""
    stats = {"entities": 0, "points": 0, "events": 0, "operators": 0,
             "evidence_points": 0, "minted_kinds": 0}
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
        is_evidence = _evidence_marked(content, evidence_turns)
        if _point_exists(proj, pid):
            # #1369 review P2: content-addressed collision across sessions —
            # OR-in this session's evidence marking (first-writer props keep
            # the session id; the raw-transcript leg mitigates attribution).
            if is_evidence:
                proj.g.query(
                    "MATCH (p:Point {id:$id}) SET p.has_answer = true",
                    params={"id": pid})
            continue
        try:
            point_props: dict = {}
            # E1 (#1533): the payload `when` slot rides onto the node only
            # when non-empty — undated sessions write no `when` prop.
            if p.get("when"):
                point_props["when"] = str(p.get("when"))
            sdk.create_point(
                "statement", content, id=pid, session_id=sid,
                lme_question_id=qid, lme_session_index=si,
                is_episodic=True, has_answer=is_evidence,
                status="draft", **point_props,
            )
            stats["points"] += 1
            if is_evidence:
                stats["evidence_points"] += 1
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
             "minted_kinds": 0, "supersessions": 0, "errors": []}

    for si, session in enumerate(sessions):
        sid = ids[si] if si < len(ids) else f"{qid}-s{si}"
        session_date = dates[si] if si < len(dates) else ""
        s_node = f"lme:{qid}:s{si}"
        evidence_turns = [str(t.get("content") or "") for t in session
                          if t.get("has_answer")]

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
        raw_id = f"lme:{qid}:s{si}:raw"
        if not _point_exists(sdk._get_proj(), raw_id):
            sdk.create_point(
                SESSION_TRANSCRIPT_KIND, _session_transcript(session),
                id=raw_id, session_id=sid, lme_question_id=qid,
                lme_session_index=si, is_episodic=True, status="draft",
            )
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
                                     session_id=s_node,
                                     session_date=session_date or None)
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
                                 evidence_turns=evidence_turns,
                                 session_date=session_date or None)
        for k in ("points", "events", "entities", "operators",
                  "evidence_points"):
            stats[k] += written.get(k, 0)

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
