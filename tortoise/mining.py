"""Conversation mining pipeline — transcript → Events + Points (+ Objects).

GAP-15 / #7003: Wire extractor.py Tier1/Tier2 → transcript → EventRecorded events.
Gate: ≥3 events/session (meeting + decision + friction/milestone).

Phase-2 (#782 / epic #264 plan W-1): an EntityStage pass reifies extracted
entities as Object nodes (deterministic obj_sha256 canonical id via
ObjectRegistered), wires aboutObject Point+Event side, aboutEvent provenance
anchors, and the extractedFrom → Source → references → Event chain. No
Subject stubs (legacy auto-detect bypassed). Dedup is a later stage — the
dedup_hits key is counted only.

Architecture (from plan WF4):
  Conversation transcript → extractor (Points + Operators) →
  Event derivation (meeting, decision, friction) → JSONL append → projection.apply()
  → EntityStage (Objects + aboutObject/aboutEvent wiring)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from typing import Any

from .api import EventAPI
from .extractor import (
    MockExtractor,
    LLMExtractor,
    extract_conversation_entities,
    _canonical_name,
)
from .ids import ulid, now_iso, content_hash

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Cue-word patterns for eventKind derivation
_DECISION_WORDS = (
    "decided", "decide", "decision", "choose", "chosen", "chose",
    "adopt", "adopted", "resolve", "resolved", "agree", "agreed",
    "commit", "committed", "confirm", "finalize", "settle",
)
_FRICTION_WORDS = (
    "disagree", "disagrees", "contradict", "contradiction",
    "conflict", "dispute", "object", "objection", "push back",
    "not agree", "wrong", "incorrect", "doesn't work",
)
_MILESTONE_WORDS = (
    "discover", "discovered", "finding", "breakthrough",
    "realize", "realized", "insight", "key learning",
    "important", "significant", "critical finding",
)


class ConversationMiner:
    """Mine conversation transcripts → Points + Operators + EventRecorded events.

    Produces ≥3 EventRecorded events per session:
      1. One "meeting" event (the session itself)
      2. "decision" events when decision language is detected
      3. "friction" events for contradictions (NAND operators) and conflict language
      4. "milestone" events for significant findings (falls back if <3 total)
    """

    def __init__(self, model: Any = None):
        # model: anything with .complete() — MockModel, OpenAICompatModel, etc.
        self.model = model

    def mine(
        self,
        transcript: str,
        source_id: str,
        api: EventAPI,
        *,
        participants: list[str] | None = None,
        session_started_at: str | None = None,
        extract_entities: bool = True,
        entity_stage=None,
    ) -> dict:
        """Run extraction pipeline on a conversation transcript.

        Returns (plan §6.1, back-compatible): the Phase-1 keys
        {events, points, operators, event_ids} plus the Phase-2 extension
        {entities, objects, dedup_hits, drafts}:
          - entities: number of extracted entity mentions
          - objects:  number of Object nodes reified/wired for those entities
          - dedup_hits: 0 — the dedup stage is a later issue; key counted only
          - drafts:  number of extraction Points created with status 'draft'

        ``entity_stage`` injects a deterministic mock (EntityStageMock, plan
        §7 preamble) for tests; None → LLM stage (or rule fallback when no
        model is configured).
        """
        started_at = session_started_at or _now()

        # 1. Run extractor: Points + IMPL/NAND operators
        extractor = self._make_extractor()
        extractor.run(transcript, source_id, api)

        # 2. Collect what was just produced by reading back from the log
        points, operators = self._collect_recent(api, source_id)

        # 3. Derive session-level EventRecorded events
        events = self._derive_events(
            transcript, source_id, points, operators,
            participants=participants,
            started_at=started_at,
        )

        # 4. Emit events via API log + projection
        event_ids = []
        for ev in events:
            event_ids.append(ev["eventId"])
            self._emit_event(api, ev)

        # 5. Phase-2 entity extraction + Object reification (W-1, DE2E-1)
        entities: list[dict] = []
        objects_wired = 0
        dedup_hits = 0  # dedup stage is a later issue (#784-ish) — key counted only
        drafts = sum(1 for p in points if p.get("status", "draft") == "draft")
        if extract_entities:
            try:
                entities = extract_conversation_entities(
                    transcript, source_id, api,
                    model=self.model, entity_stage=entity_stage,
                )
            except Exception:
                logger.exception("entity extraction failed for %s", source_id)
                entities = []
            objects_wired = self._reify_entities(
                transcript, source_id, api, entities, points, events,
            )

        return {
            "events": len(events),
            "points": len(points),
            "operators": len(operators),
            "event_ids": event_ids,
            "entities": len(entities),
            "objects": objects_wired,
            "dedup_hits": dedup_hits,
            "drafts": drafts,
        }

    # ── Phase-2 entity reification (W-1 write phase, DE2E-1) ──────

    @staticmethod
    def _object_id(name: str) -> str:
        """Deterministic canonical Object id (plan §4.1/§4.4): ``obj_`` +
        sha256 of the domain-separated canonical name, truncated to 16 hex
        chars (64 bits — collision-resistant vs the old 48-bit [:12], which
        collided once punctuation-stripping canonicalization was added,
        "Foo.Bar" == "FooBar") — via the existing ids.content_hash helper
        (no third sha256)."""
        return "obj_" + content_hash(f"obj:{_canonical_name(name)}")[:16]

    def _reify_entities(
        self,
        transcript: str,
        source_id: str,
        api: EventAPI,
        entities: list[dict],
        points: list[dict],
        events: list[dict],
    ) -> int:
        """W-1 write phase (plan §4.1/§4.4, DE2E-1): reify each extracted
        entity as an Object node via the reification rule (ObjectRegistered
        event + deterministic canonical id), then wire:

          - (Point)-[:aboutObject]->(Object) for Points mentioning the entity
          - (Event)-[:aboutObject]->(Object) for session events about the entity
          - (Point)-[:aboutEvent]->(meeting Event) provenance anchor
          - (Source)-[:references]->(Event) completing the chain
            Point -[:extractedFrom]-> Source -[:references]-> Event

        Deliberately bypasses the legacy _create_about_edges auto-detect path
        (projection/edges.py:69-111) so no :Subject stub nodes are created for
        extracted entities (DE2E-1 step 6). Returns the number of Object nodes
        reified. In log-only mode (no projection) ObjectRegistered events are
        emitted for the audit trail and the count is returned without wiring.
        """
        proj = api.projection
        if proj is None:
            # Log-only mode: emit ObjectRegistered events for the audit trail;
            # graph wiring is projection-bound.
            wired = 0
            for ent in entities:
                name = ent["name"]
                if not self._reifiable_name(name, transcript):
                    continue
                api.add_object(name, ent["objectKind"], id=self._object_id(name),
                               canonical_name=_canonical_name(name), title=name)
                wired += 1
            return wired

        meeting_event = f"meeting-{source_id}"
        wired = 0
        for ent in entities:
            name = ent["name"]
            if not self._reifiable_name(name, transcript):
                continue
            canonical = _canonical_name(name)
            api.add_object(name, ent["objectKind"], id=self._object_id(name),
                           canonical_name=canonical, title=name)
            # MERGE-by-name: re-fetch the canonical node id (a prior run's id
            # wins — no duplicate Objects, DE2E-8 idempotency)
            r = proj.g.query("MATCH (o:Object {name:$name}) RETURN o.id",
                             params={"name": name})
            oid = r.result_set[0][0] if r.result_set and r.result_set[0] \
                else self._object_id(name)
            # Point side: deterministic mention-based wiring
            for p in points:
                content = p.get("content", "")
                if name.lower() in content.lower():
                    proj.create_about_edge(p["id"], oid, "aboutObject")
            # Event side: the meeting event always; derived events whose
            # object text mentions the entity
            proj.create_about_edge(meeting_event, oid, "aboutObject")
            for ev in events:
                if ev.get("eventId") != meeting_event and \
                        name.lower() in str(ev.get("object", "")).lower():
                    proj.create_about_edge(ev["eventId"], oid, "aboutObject")
            wired += 1

        # Point → aboutEvent → meeting Event (session-occurrence anchor, DE2E-1)
        for p in points:
            proj.create_about_edge(p["id"], meeting_event, "aboutEvent")
        # Complete the provenance chain: Source -[:references]-> Event
        proj.link_source_to_entity(source_id, meeting_event, "Event")
        return wired

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _reifiable_name(name: str, transcript: str) -> bool:
        """DE2E-review (substring wiring): a name must be ≥3 chars and appear
        verbatim in the transcript before it is reified/wired — otherwise
        short or absent names over-wire via substring matches ("port 16379"
        vs "port 1637")."""
        name = (name or "").strip()
        return len(name) >= 3 and name.lower() in transcript.lower()

    def _make_extractor(self):
        if self.model is not None:
            return LLMExtractor(self.model, self.model)
        return MockExtractor()

    def _collect_recent(self, api: EventAPI,
                        source_id: str | None = None) -> tuple[list[dict], list[dict]]:
        """Collect PointAdded and OperatorAdded events from the current run.

        Run boundary (DE2E-review): filters by ``api.current_run`` when set AND
        by provenance source_id when given. A corpus loop reusing ONE EventAPI
        across files must never inherit a prior file's points — otherwise
        cross-session aboutObject/aboutEvent wiring, wrong per-session event
        content, duplicate derived events, and re-mine stacking occur.
        """
        run_id = getattr(api, "current_run", None)
        points: list[dict] = []
        operators: list[dict] = []
        for ev in api.log.read_all():
            if ev.get("type") == "PointAdded":
                p = ev.get("point", {})
                prov = p.get("provenance", {})
                if run_id and prov.get("run_id") != run_id:
                    continue
                if source_id is not None and prov.get("source_id") != source_id:
                    continue
                points.append(p)
            elif ev.get("type") == "OperatorAdded":
                op = ev.get("point", {})
                prov = op.get("provenance", {})
                if run_id and prov.get("run_id") != run_id:
                    continue
                if source_id is not None and prov.get("source_id") != source_id:
                    continue
                operators.append(op)
        return points, operators

    def _derive_events(
        self,
        transcript: str,
        source_id: str,
        points: list[dict],
        operators: list[dict],
        *,
        participants: list[str] | None = None,
        started_at: str | None = None,
    ) -> list[dict]:
        """Derive session-level EventRecorded events from extraction results."""
        ts = started_at or _now()
        source = f"conversation:{source_id}"
        parts = participants or self._extract_participants(transcript)

        events: list[dict] = []

        # Event 1: The meeting itself
        events.append(self._make_event(
            event_id=f"meeting-{source_id}",
            event_kind="meeting",
            subject=f"transcript:{source_id}",
            object=self._summary_line(transcript, points),
            source=source,
            participants=parts,
            started_at=ts,
        ))

        # Event 2-n: Decisions (from cue words in point content)
        decision_idx = 1
        for p in points:
            content = p.get("content", "")
            if self._has_cue(content, _DECISION_WORDS):
                events.append(self._make_event(
                    event_id=f"decision-{source_id}-{decision_idx}",
                    event_kind="decision",
                    subject=parts[0] if parts else "unknown",
                    object=content[:200],
                    source=source,
                    participants=parts,
                    started_at=ts,
                    parent_event=f"meeting-{source_id}",
                ))
                decision_idx += 1

        # Event n+: Friction (from NAND operators + conflict language)
        friction_idx = 1
        for op in operators:
            otype = op.get("operator", {}).get("op_type", "")
            if otype == "NAND":
                events.append(self._make_event(
                    event_id=f"friction-{source_id}-{friction_idx}",
                    event_kind="friction",
                    subject="extraction",
                    object=op.get("content", "")[:200],
                    source=source,
                    participants=parts,
                    started_at=ts,
                    parent_event=f"meeting-{source_id}",
                ))
                friction_idx += 1

        # Check point content for friction/conflict language (in addition to NAND operators)
        # A point already covered by a NAND operator is skipped — NAND is the
        # stronger signal and was already captured above (issue #325).
        nand_inputs = {
            iid
            for op in operators
            if op.get("operator", {}).get("op_type") == "NAND"
            for iid in op.get("operator", {}).get("inputs", [])
        }
        seen_friction_content: set[str] = set()
        for p in points:
            content = p.get("content", "")
            if p.get("id") not in nand_inputs and self._has_cue(content, _FRICTION_WORDS):
                # Dedup by full content — the same conflict repeated across
                # points must not flood the log with identical friction events
                # (a 100-char prefix could collide between distinct conflicts).
                if content in seen_friction_content:
                    continue
                seen_friction_content.add(content)
                events.append(self._make_event(
                    event_id=f"friction-{source_id}-{friction_idx}",
                    event_kind="friction",
                    subject="extraction",
                    object=content[:200],
                    source=source,
                    participants=parts,
                    started_at=ts,
                    parent_event=f"meeting-{source_id}",
                ))
                friction_idx += 1

        # Fallback: if <3 events, add milestone events for significant content
        milestone_idx = 1
        while len(events) < 3:
            # Pick the next significant point as a milestone
            for p in points:
                content = p.get("content", "")
                if len(content) > 60 and self._has_cue(content, _MILESTONE_WORDS):
                    # Check if already captured as another event type
                    already = any(
                        e["eventKind"] in ("decision", "friction")
                        and content[:100] in e.get("object", "")
                        for e in events
                    )
                    if not already:
                        events.append(self._make_event(
                            event_id=f"milestone-{source_id}-{milestone_idx}",
                            event_kind="milestone",
                            subject=parts[0] if parts else "unknown",
                            object=content[:200],
                            source=source,
                            participants=parts,
                            started_at=ts,
                            parent_event=f"meeting-{source_id}",
                        ))
                        milestone_idx += 1
                        if len(events) >= 3:
                            break
            break  # ponytail: single pass only

        # Absolute fallback: add generic session event
        fallback_idx = 1
        while len(events) < 3:
            # Capture longest point as generic observation
            if points:
                longest = max(points, key=lambda p: len(p.get("content", "")))
                content = longest.get("content", "")
                if content and not any(content[:100] in e.get("object", "") for e in events):
                    events.append(self._make_event(
                        event_id=f"observation-{source_id}-{fallback_idx}",
                        event_kind="observation",
                        subject=parts[0] if parts else "unknown",
                        object=content[:200],
                        source=source,
                        participants=parts,
                        started_at=ts,
                        parent_event=f"meeting-{source_id}",
                    ))
                    fallback_idx += 1
                else:
                    break  # can't produce more unique events
            else:
                break  # no points at all

        return events

    @staticmethod
    def _emit_event(api: EventAPI, event: dict) -> None:
        """Emit an EventRecorded event to the log + projection."""
        record = {
            "version": "1.0",
            "type": "EventRecorded",
            "event": event,
            "createdAt": _now(),
        }
        api.log.append(record)
        if api.projection is not None:
            api.projection.apply(record)

    @staticmethod
    def _make_event(
        *,
        event_id: str,
        event_kind: str,
        subject: str,
        object: str,
        source: str,
        participants: list[str] | None = None,
        started_at: str | None = None,
        parent_event: str | None = None,
    ) -> dict:
        return {
            "eventId": event_id,
            "eventKind": event_kind,
            "subject": subject,
            "object": object,
            "startedAt": started_at or _now(),
            "endedAt": None,
            "parentEvent": parent_event,
            "childEvents": [],
            "participants": participants or [],
            "classificationLevel": "internal",
            "format": "jsonl",
            "source": source,
        }

    @staticmethod
    def _has_cue(text: str, words: tuple[str, ...]) -> bool:
        low = text.lower()
        return any(w in low for w in words)

    @staticmethod
    def _summary_line(transcript: str, points: list[dict]) -> str:
        """First meaningful line for a brief meeting object."""
        for p in points:
            content = p.get("content", "")
            if len(content) >= 30:
                return content[:200]
        # fallback: first non-empty line from transcript
        for line in transcript.splitlines():
            stripped = line.strip()
            if len(stripped) > 20 and ":" in stripped:
                return stripped[stripped.index(":") + 1:].strip()[:200]
        return transcript.splitlines()[0][:200] if transcript else "(empty)"

    @staticmethod
    def _extract_participants(transcript: str) -> list[str]:
        """Extract unique speaker names from transcript."""
        speaker_re = re.compile(r"^\s*([A-Z][\w .'-]{0,40}):", re.MULTILINE)
        seen = set()
        for m in speaker_re.finditer(transcript):
            name = m.group(1).strip()
            if name and name not in seen:
                seen.add(name)
        return sorted(seen)


# ── Module-level convenience ────────────────────────────────────────

def mine_conversation(
    transcript: str,
    source_id: str,
    api: EventAPI,
    *,
    model: Any | None = None,
    participants: list[str] | None = None,
    extract_entities: bool = True,
    entity_stage=None,
    content_dedup: bool = True,
    dedup_threshold: float | None = None,
) -> dict:
    """Convenience: mine a conversation transcript → Events + Points + Objects.

    Returns (plan §6.1, back-compatible): Phase-1 keys {events, points,
    operators, event_ids} plus Phase-2 {entities, objects, dedup_hits, drafts}.
    ``content_dedup``/``dedup_threshold`` are accepted for the pinned API
    surface but the dedup stage itself is a later issue — dedup_hits is
    always 0 from this entry point (DE2E-3).
    """
    if content_dedup:
        logger.info("content dedup stage not yet implemented — "
                    "dedup_hits always 0 (DE2E-3)")
    miner = ConversationMiner(model)
    return miner.mine(transcript, source_id, api,
                      participants=participants,
                      extract_entities=extract_entities,
                      entity_stage=entity_stage)


def mine_corpus(
    directory: str,
    *,
    extract_entities: bool = True,
    progress_file: str | None = None,
    model: Any | None = None,
    event_log_path: str | None = None,
) -> dict:
    """Batch-mine a session corpus (J-1, plan §6.1) into a fresh embedded DB.

    Convenience wrapper over :func:`mine_corpus_with_sdk` — creates an
    isolated embedded graph next to the corpus directory. Callers that want
    to mine into an existing team graph use ``TortoiseSDK.mine_corpus``.
    ``event_log_path`` routes mining events to the given JSONL log (default:
    the SDK's configured event log, or a fallback next to the DB path).
    """
    import os as _os
    import hashlib as _hashlib
    from tortoise.sdk import TortoiseSDK

    db_path = _os.path.join(
        _os.path.dirname(_os.path.abspath(directory)) or ".",
        f".mine-{_hashlib.md5(directory.encode()).hexdigest()[:8]}.db",
    )
    sdk = TortoiseSDK(db_path, event_log_path=event_log_path)
    try:
        return mine_corpus_with_sdk(
            sdk, directory,
            extract_entities=extract_entities,
            progress_file=progress_file,
            model=model,
            event_log_path=event_log_path,
        )
    finally:
        sdk.close()


def mine_corpus_with_sdk(
    sdk,
    directory: str,
    *,
    extract_entities: bool = True,
    progress_file: str | None = None,
    model: Any | None = None,
    event_log_path: str | None = None,
) -> dict:
    """Batch-mine a session corpus (J-1, plan §6.1) through an existing SDK.

    COMPOSES the SDK's ingest_corpus (security, resume, file_hash — R17): the
    directory is validated and files ingested (AgentSession event index) by
    the shared machinery; this pass then mines every file whose content hash
    does not already match its indexed Event (file_hash skip — DE2E-N8).

    R17 / review hardening (in addition to ingest's own gate):
    - the directory security validation (ingest_dir_is_safe) runs BEFORE any
      file I/O — the pre-index scan never reads an unsafe directory;
    - symlinked *.md entries are never read (host-file read + LLM
      exfiltration when model= is set) — surfaced as non-retryable errors;
    - duplicate-sessionId files are deduped to ONE primary per sessionId
      (first in sorted order, mirroring ingest) — non-primary copies are
      skipped and surfaced as non-retryable errors;
    - a fresh run boundary (api.current_run) is set per file so each
      session's mine sees only its own points (no cross-session wiring).

    ``event_log_path`` routes mining events to the given JSONL log; default:
    the SDK's configured event log, else a fallback next to the DB path.

    Returns {sessions, ingested, updated, skipped, failed, entities, objects,
    dedup_hits, drafts, errors:[{file, error, retryable}]}.
    """
    import os as _os
    import hashlib as _hashlib
    from pathlib import Path

    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from .security import ingest_dir_is_safe

    # ── R17: directory security validation BEFORE any file I/O. The pre-index
    # scan reads host files — an unsafe directory (relative, `..`, or outside
    # TORTOISE_INGEST_BASE_DIR) must never be walked here, matching
    # ingest_corpus's own gate (which runs later, after this scan). ──
    ingest_base = None
    raw_base = _os.environ.get("TORTOISE_INGEST_BASE_DIR")
    if raw_base:
        ingest_base = _os.path.realpath(_os.path.expanduser(raw_base))
    if not ingest_dir_is_safe(directory, ingest_base):
        raise ValueError(
            f"Unsafe ingest directory: {directory!r}. Directory must be "
            f"absolute, contain no '..' components, and resolve under "
            f"TORTOISE_INGEST_BASE_DIR when set ({ingest_base or '<unset>'})."
        )

    # ── Pre-ingest scan: capture which files are ALREADY indexed with the
    # CURRENT content hash (unchanged re-run / duplicate session file,
    # DE2E-N8). Must run BEFORE ingest_corpus so first-run files are not
    # mistaken for already-indexed. Symlinks are skipped (R17); duplicate
    # sessionIds are deduped to one primary per sessionId (#280 parity). ──
    from .session_indexer import _FM_RE
    proj = sdk._get_proj()
    errors: list[dict] = []
    files: list[Path] = []
    scanned: list[str] = []  # every *.md found by the walk (sessions count)
    pre_indexed: set[str] = set()
    _frontmatter_of: dict[str, dict] = {}
    _primary_sessions: dict[str, str] = {}
    for fp in sorted(Path(directory).rglob("*.md")):
        rel = str(fp)
        scanned.append(rel)
        if fp.is_symlink():
            errors.append({"file": rel,
                           "error": "symlinked file skipped (R17: the corpus "
                                     "walk must not follow symlinks)",
                           "retryable": False})
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            # unreadable file — surface it (non-retryable) and skip; ingest
            # counts it as failed but the mining-pass errors list must too
            errors.append({"file": rel, "error": str(e), "retryable": False})
            continue
        m = _FM_RE.match(text)
        frontmatter: dict = {}
        if m:
            try:
                import yaml as _yaml
                parsed = _yaml.safe_load(m.group(1))
                if isinstance(parsed, dict):
                    frontmatter = parsed
            except Exception:
                pass
        _frontmatter_of[rel] = frontmatter
        session_id = frontmatter.get("sessionId") or frontmatter.get("session_id") \
            or f"file_{fp.stem}"
        # #280 parity: duplicate sessionId → first-in-sorted-order file wins;
        # non-primary copies are skipped (deterministic, non-retryable —
        # re-running changes nothing) instead of being re-mined every run
        # (event flapping, LLM spend, non-convergence).
        _primary = _primary_sessions.get(session_id)
        if _primary is not None and _primary != rel:
            errors.append({"file": rel,
                           "error": f"duplicate sessionId '{session_id}' "
                                    f"(primary file: {_primary}) — non-primary "
                                    f"copy skipped",
                           "retryable": False})
            continue
        _primary_sessions.setdefault(session_id, rel)
        file_hash = _hashlib.sha256(text.encode()).hexdigest()
        rows = proj.g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.file_hash",
            params={"eid": f"session_{session_id}"},
        ).result_set
        if rows and rows[0] and rows[0][0] == file_hash:
            pre_indexed.add(rel)
        files.append(fp)

    ingest = sdk.ingest_corpus(
        directory, eventKind="AgentSession", extract_metadata=False,
        progress_file=progress_file,
    )

    # ── Event-log routing (DE2E-review): mine through the SDK's own event
    # log when one exists (events stay on the canonical store path); an
    # explicit event_log_path overrides; the fallback lives NEXT TO THE DB
    # PATH (not next to the corpus dir). ──
    log = None
    if event_log_path is not None:
        log = EventLog(event_log_path)
    elif sdk._get_event_log() is not None:
        log = sdk._get_event_log()
    else:
        db_path = getattr(sdk, "_db_path", None)
        anchor = (_os.path.dirname(_os.path.abspath(db_path)) if db_path
                  else _os.path.dirname(_os.path.abspath(directory)) or ".")
        log = EventLog(_os.path.join(
            anchor,
            f".mine-events-{_hashlib.md5(directory.encode()).hexdigest()[:8]}.jsonl",
        ))
    api = EventAPI(log, initiated_by="extractor", agent_id="mining-pilot",
                   projection=proj)
    api._ingest_cache = {}  # mining always processes fresh (CLI parity)

    miner = ConversationMiner(model)
    entities = objects = dedup_hits = drafts = 0
    for fp in files:
        rel = str(fp)
        if rel in pre_indexed:
            continue  # unchanged re-run — ingest reported it as skipped
        try:
            text = fp.read_text(encoding="utf-8")
        except Exception as e:
            errors.append({"file": rel, "error": str(e), "retryable": False})
            continue
        frontmatter = _frontmatter_of.get(rel, {})
        session_id = frontmatter.get("sessionId") or frontmatter.get("session_id") \
            or f"file_{fp.stem}"
        if _primary_sessions.get(session_id, rel) != rel:
            continue  # non-primary copy of a duplicate sessionId (already errored)
        # Run boundary: one fresh run per file, so _collect_recent never
        # inherits a prior file's points (cross-session aboutObject/aboutEvent
        # wiring, wrong per-session event content, re-mine stacking).
        api.current_run = ulid()
        try:
            res = miner.mine(text, f"session_{session_id}", api,
                             extract_entities=extract_entities,
                             participants=ConversationMiner._extract_participants(text))
            entities += res["entities"]
            objects += res["objects"]
            dedup_hits += res["dedup_hits"]
            drafts += res["drafts"]
        except Exception as e:
            errors.append({"file": rel, "error": str(e), "retryable": True})

    # failed = union of per-file failures across both passes (a file that
    # fails ingest AND mining is counted once)
    failed_files = {e.get("file") for e in ingest.get("errors", [])} | \
        {e.get("file") for e in errors}

    return {
        "sessions": len(scanned),
        "ingested": ingest.get("ingested", 0),
        "updated": ingest.get("updated", 0),
        "skipped": ingest.get("skipped", 0),
        "failed": len(failed_files),
        "entities": entities,
        "objects": objects,
        "dedup_hits": dedup_hits,
        "drafts": drafts,
        "errors": errors,
    }
