"""Conversation mining pipeline — transcript → Events + Points.

GAP-15 / #7003: Wire extractor.py Tier1/Tier2 → transcript → EventRecorded events.
Gate: ≥3 events/session (meeting + decision + friction/milestone).

Architecture (from plan WF4):
  Conversation transcript → extractor (Points + Operators) →
  Event derivation (meeting, decision, friction) → JSONL append → projection.apply()
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from typing import Any

from .api import EventAPI
from .extractor import MockExtractor, LLMExtractor
from .ids import ulid, now_iso


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
    ) -> dict:
        """Run extraction pipeline on a conversation transcript.

        Returns: {events: N, points: M, operators: K, event_ids: [...]}
        """
        started_at = session_started_at or _now()

        # 1. Run extractor: Points + IMPL/NAND operators
        extractor = self._make_extractor()
        extractor.run(transcript, source_id, api)

        # 2. Collect what was just produced by reading back from the log
        points, operators = self._collect_recent(api)

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

        return {
            "events": len(events),
            "points": len(points),
            "operators": len(operators),
            "event_ids": event_ids,
        }

    # ── Internal helpers ───────────────────────────────────────────

    def _make_extractor(self):
        if self.model is not None:
            return LLMExtractor(self.model, self.model)
        return MockExtractor()

    def _collect_recent(self, api: EventAPI) -> tuple[list[dict], list[dict]]:
        """Collect PointAdded and OperatorAdded events from the current run."""
        run_id = getattr(api, "current_run", None)
        points: list[dict] = []
        operators: list[dict] = []
        for ev in api.log.read_all():
            if ev.get("type") == "PointAdded":
                p = ev.get("point", {})
                if run_id and p.get("provenance", {}).get("run_id") == run_id:
                    points.append(p)
                elif not run_id:
                    points.append(p)
            elif ev.get("type") == "OperatorAdded":
                op = ev.get("point", {})
                if run_id and op.get("provenance", {}).get("run_id") == run_id:
                    operators.append(op)
                elif not run_id:
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
) -> dict:
    """Convenience: mine a conversation transcript → Events + Points.

    Returns: {events: N, points: M, operators: K, event_ids: [...]}
    """
    miner = ConversationMiner(model)
    return miner.mine(transcript, source_id, api, participants=participants)
