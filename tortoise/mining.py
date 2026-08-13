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


# ── Phase-4 EP-safe batch commit (#785, epic 2026-08-09-insight-mining-p2p4) ──
# W-3 state machine: a batch is ACTIVE while being written, QUARANTINED when
# W-3 verification fails (EP drift / batch failure), COMMITTED when W-3 passes
# (drafts enter the review queue; quarantine recovery = re-run pass → committed).
# Quarantine is BATCH-level (plan §3 state machine) — NOT a Point status:
# a quarantined batch's Points stay `draft` until re-review. State lives in a
# `:Batch` marker node (operational metadata, like :GraphEvent — not ONTOLOGY
# content; no new Point statuses, POINT_STATUS_VALUES unchanged).
#
# NOTE (review #944): :Batch state is NOT JSONL-rebuilt — rebuild_all wipes
# marker nodes (they are not :Point nodes and no batch-state events exist),
# so after a rebuild quarantined batches become unregistered and their Points
# are promotable again. Tracked with the W-3 pipeline wiring follow-up; until
# then, quarantine is an in-session safety lock, not a durable one.
BATCH_STATUS_ACTIVE = "active"
BATCH_STATUS_QUARANTINED = "quarantined"
BATCH_STATUS_COMMITTED = "committed"
BATCH_STATUS_VALUES = frozenset({
    BATCH_STATUS_ACTIVE, BATCH_STATUS_QUARANTINED, BATCH_STATUS_COMMITTED,
})


# Grounding drift ceiling (W-3 / Gate B): snapshot mean grounding of live
# non-operator Points pre/post batch must not move more than 2% mean absolute.
def _default_grounding_fn():
    """Resolve the Gate B `mean_grounding()` helper (plan §7 DE2E-4).

    TODO(#779): mean_grounding() lands with the Gate B tooling issue. Until
    then the grounding gate is RECORDED as unavailable/skipped (never crashes
    the commit), while the structural W-3 checks (draft-only + no auto-wire)
    stay hard gates. The interface is pinned: ``mean_grounding() -> float``
    (mean over `confidence` of live non-operator Points, full live Point set).
    """
    try:
        from tortoise.analyze import mean_grounding  # type: ignore[attr-defined]
        return mean_grounding
    except Exception:  # noqa: BLE001 — #779 not merged yet
        return None


def _batch_node(proj, batch_id: str) -> dict | None:
    """Raw :Batch node properties, or None when the batch has no state node."""
    rows = proj.g.query(
        "MATCH (b:Batch {id:$id}) RETURN properties(b)",
        params={"id": batch_id},
    ).result_set
    return rows[0][0] if rows else None


def batch_status(proj, batch_id: str) -> dict | None:
    """Read a batch's lifecycle state: {batch_id, status, reason, ...} | None.

    None = no :Batch node (batch not registered — nothing quarantined). Used by
    promote_point (sdk.py) for the quarantine lock.
    """
    node = _batch_node(proj, batch_id)
    if node is None:
        return None
    return {
        "batch_id": batch_id,
        "status": node.get("status", BATCH_STATUS_ACTIVE),
        "reason": node.get("reason"),
        "quarantinedAt": node.get("quarantinedAt"),
        "committedAt": node.get("committedAt"),
        "pointCount": node.get("pointCount"),
    }


def _upsert_batch(proj, batch_id: str, *, status: str,
                  reason: str | None = None,
                  point_count: int | None = None) -> dict:
    """Create-or-update the :Batch state node (idempotent)."""
    if not batch_id or not isinstance(batch_id, str):
        raise ValueError(f"batch_id must be a non-empty string, got {batch_id!r}")
    if status not in BATCH_STATUS_VALUES:
        raise ValueError(
            f"Invalid batch status {status!r}. Must be one of: "
            f", ".join(sorted(BATCH_STATUS_VALUES)))
    now = _now()
    sets = ["b.status=$status", "b.updatedAt=$now"]
    params = {"id": batch_id, "status": status, "now": now}
    if reason is not None:
        sets.append("b.reason=$reason")
        params["reason"] = reason
    if status == BATCH_STATUS_QUARANTINED:
        # preserve the original quarantine timestamp on re-quarantine
        sets.append("b.quarantinedAt=coalesce(b.quarantinedAt, $now)")
    if status == BATCH_STATUS_COMMITTED:
        sets.append("b.committedAt=$now")  # review #944: declared field was never written
        if reason is None:
            sets.append("b.reason=null")  # cleared on recovery
            # quarantinedAt is EPISODE state — clear it when the batch
            # recovers so a later re-quarantine records the NEW episode
            # (#944 review).
            sets.append("b.quarantinedAt=null")
    if point_count is not None:
        sets.append("b.pointCount=$pointCount")
        params["pointCount"] = point_count
    proj.g.query(
        "MERGE (b:Batch {id:$id}) SET " + ", ".join(sets),
        params=params,
    )
    return batch_status(proj, batch_id)


def quarantine_batch(proj, batch_id: str, *, reason: str) -> dict:
    """Mark a batch quarantined — blocks promote_point on its Points (W-3).

    Test/ops primitive (plan §6.1): quarantine is the drift-fail path of
    EpSafeCommit; callers that force it directly (tests, ops) must pass the
    documented W-3 failure reason. Idempotent — re-quarantine updates the
    reason and keeps the original quarantine timestamp.
    """
    if not reason or not isinstance(reason, str):
        raise ValueError("quarantine_batch requires a non-empty reason")
    _upsert_batch(proj, batch_id, status=BATCH_STATUS_QUARANTINED, reason=reason)
    return {
        "batch_id": batch_id,
        "blocked": True,
        "reason": reason,
        "status": BATCH_STATUS_QUARANTINED,
    }


def list_quarantined(proj) -> list[dict]:
    """List all quarantined batches: [{batch_id, reason, quarantinedAt, ...}] (newest first)."""
    rows = proj.g.query(
        "MATCH (b:Batch {status:$st}) RETURN properties(b) "
        "ORDER BY b.updatedAt DESC",
        params={"st": BATCH_STATUS_QUARANTINED},
    ).result_set
    out = []
    for (props,) in rows:
        bid = props.get("id")
        if bid is None:
            continue
        out.append({
            "batch_id": bid,
            "status": BATCH_STATUS_QUARANTINED,
            "reason": props.get("reason"),
            "quarantinedAt": props.get("quarantinedAt"),
            "pointCount": props.get("pointCount"),
        })
    return out


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
        batch_id: str | None = None,
        content_dedup: bool = True,
        dedup_threshold: float = 0.60,
        sdk=None,
    ) -> dict:
        """Run extraction pipeline on a conversation transcript.

        Returns (plan §6.1, back-compatible): the Phase-1 keys
        {events, points, operators, event_ids} plus the Phase-2 extension
        {entities, objects, dedup_hits, drafts}:
          - entities: number of extracted entity mentions
          - objects:  number of Object nodes reified/wired for those entities
          - dedup_hits: number of duplicate decision Points flagged by the
            two-tier content dedup (W-2, #784); dedup_wired/dedup_deferred
            break down draft-prior (wired) vs live-prior (deferred) hits
          - drafts:  number of extraction Points created with status 'draft'

        Phase-4 (W-3, #990): every extraction Point is stamped with a
        ``batch_id`` (auto-generated per call when not given, plan §4.4) and
        the batch runs the EpSafeCommit W-3 gate (draft-only, no auto-wire,
        grounding drift ≤2% via mean_grounding — #779). Additive result keys:
          - batch_id: the batch this call produced
          - batch_status: "committed" (W-3 pass — drafts enter the review
            queue), "quarantined" (W-3 fail — promote_point is blocked on
            the batch's Points until a re-run passes, J-5), or "not_gated"
            (standalone log mode — no projection, gate skipped)
          - batch_reason: the W-3 failure reason when quarantined, or the
            skip explanation when not_gated

        ``entity_stage`` injects a deterministic mock (EntityStageMock, plan
        §7 preamble) for tests; None → LLM stage (or rule fallback when no
        model is configured).
        """
        started_at = session_started_at or _now()
        batch_id = batch_id or ulid()

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

        # Phase-4 (W-3, #990): stamp the batch on every extraction Point
        # (post-hoc bulk write — the extractor is untouched), then run the
        # EpSafeCommit gate over the batch. Pass → committed (drafts enter
        # the review queue); fail → batch quarantined (promote_point blocks).
        point_ids = [p.get("id") for p in points if p.get("id")]
        if point_ids and api.projection is not None:
            api.projection.g.query(
                "MATCH (n:Point) WHERE n.id IN $ids SET n.batch_id = $bid",
                params={"ids": point_ids, "bid": batch_id},
            )
        # W-2 content dedup (#784): flag duplicate decision Points against
        # existing decisions (hash + embedding tiers); draft priors get the
        # "already decided" IMPL wired immediately, live priors defer to
        # promotion (Variant C). Requires the SDK for operator creation —
        # flag-only when sdk is None.
        dedup_report = None
        dedup_error = None
        if content_dedup and point_ids and sdk is not None:
            try:
                dedup_report = sdk._dedup_content_candidates(
                    point_ids, threshold=dedup_threshold, sdk_for_wiring=sdk)
            except Exception as exc:
                # Surface, don't swallow: a silent no-op made the feature look
                # like 'no duplicates found' (#784 review).
                logger.exception("content dedup failed for batch %s", batch_id)
                dedup_error = f"content dedup failed: {exc}"

        batch_status = "not_gated"
        batch_reason = "no projection — W-3 gate skipped (standalone log mode)"
        if api.projection is not None:
            from tortoise.analyze import grounding_snapshot
            # EpSafeCommit/quarantine_batch are module-level (defined below);
            # reference via module for clarity at the call site.
            from . import mining as _mining
            try:
                # Grounding drift protects the LIVE graph against batch
                # side-effects. Actual ordering: extraction has already
                # completed here, and extraction writes only drafts (excluded
                # from the live-only mean), so pre and post normally agree
                # (~0 drift) — the check guards against concurrent live
                # changes landing between the two reads (e.g. a parallel
                # promote_point on the same graph), which would trip the
                # ≤2% mean ceiling and quarantine the batch.
                pre = grounding_snapshot(api.projection)["mean"]
                gate = _mining.EpSafeCommit(api.projection, batch_id)
                res = gate.run(point_ids, grounding_before=pre,
                               grounding_after=grounding_snapshot(
                                   api.projection)["mean"])
                if res["ok"]:
                    batch_status = "committed"
                    batch_reason = None
                else:
                    batch_status = "quarantined"
                    batch_reason = res.get("reason")
            except Exception:
                logger.exception("W-3 gate failed for batch %s — quarantining", batch_id)
                try:
                    _mining.quarantine_batch(api.projection, batch_id,
                                             reason="W-3 gate error (pipeline wiring)")
                except Exception:
                    logger.exception("quarantine_batch failed for %s", batch_id)
                batch_status = "quarantined"
                batch_reason = "W-3 gate error (pipeline wiring)"

        return {
            "events": len(events),
            "points": len(points),
            "operators": len(operators),
            "event_ids": event_ids,
            "entities": len(entities),
            "objects": objects_wired,
            "dedup_hits": dedup_hits,
            "drafts": drafts,
            "batch_id": batch_id,
            "batch_status": batch_status,
            "batch_reason": batch_reason,
            "dedup_hits": (dedup_report or {}).get("hits", dedup_hits),
            "dedup_wired": (dedup_report or {}).get("wired_draft_to_draft", 0),
            "dedup_deferred": (dedup_report or {}).get("deferred_live_prior", 0),
            "dedup_error": dedup_error,
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
    sdk=None,
) -> dict:
    """Convenience: mine a conversation transcript → Events + Points + Objects.

    Returns (plan §6.1, back-compatible): Phase-1 keys {events, points,
    operators, event_ids} plus Phase-2 {entities, objects, dedup_hits, drafts}
    plus Phase-4 {batch_id, batch_status, batch_reason} (W-3 wiring, #990 —
    see ConversationMiner.mine for the exact semantics, incl. the
    "not_gated" standalone-log state).
    ``content_dedup`` (default True) enables the two-tier content dedup for
    decision Points (W-2, #784); ``dedup_threshold`` defaults to the pinned
    review band 0.60 (θ from the calibration milestone). Pass ``sdk=`` to
    enable operator wiring (draft-prior alreadyDecided links); without it
    dedup is skipped entirely (a warning is logged).
    """
    if content_dedup and sdk is None:
        logger.warning(
            "content dedup enabled but no sdk passed to mine_conversation — "
            "dedup is skipped entirely (pass sdk= to enable; #784)")
    miner = ConversationMiner(model)
    return miner.mine(transcript, source_id, api,
                      participants=participants,
                      extract_entities=extract_entities,
                      entity_stage=entity_stage,
                      batch_id=None,  # auto-generated per call (#990)
                      content_dedup=content_dedup,
                      dedup_threshold=dedup_threshold,
                      sdk=sdk)


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
    content_dedup: bool = True,
    dedup_threshold: float = 0.60,
) -> dict:
    """Batch-mine a session corpus (J-1, plan §6.1) through an existing SDK.

    COMPOSES the SDK's ingest_corpus (security, resume, file_hash — R17): the
    directory is validated and files ingested (AgentSession event index) by
    the shared machinery; this pass then mines every file whose content hash
    does not already match its indexed Event AND whose session Event carries
    a mined marker for that exact hash (mined_hash skip — DE2E-N8, round-2
    review: file_hash alone conflates "indexed by ingest" with "already
    mined", so a corpus indexed by a standalone ingest_corpus was never
    mined).

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

    # ── Pre-ingest scan: capture which files are ALREADY indexed AND mined
    # with the CURRENT content hash (unchanged re-run / duplicate session
    # file, DE2E-N8). Must run BEFORE ingest_corpus so first-run files are
    # not mistaken for already-indexed. The skip requires BOTH the session
    # Event's file_hash AND its mined_hash (stamped after a successful mine
    # below) to equal the current content hash — ingest_corpus always
    # creates/updates the session Event (file_hash set) INCLUDING on the
    # first mine, so a file_hash-only match would silently skip a corpus
    # that a standalone ingest_corpus indexed but never mined (the
    # documented "mine into an existing team graph" flow no-ops; round-2
    # review). Symlinks are skipped (R17); duplicate sessionIds are deduped
    # to one primary per sessionId (#280 parity). ──
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
            "MATCH (e:Event {eventId:$eid}) RETURN e.file_hash, e.mined_hash",
            params={"eid": f"session_{session_id}"},
        ).result_set
        # Round-2 review: skip only when the session was BOTH indexed at this
        # content hash AND actually mined at this content hash. A session
        # Event created by a standalone ingest_corpus has file_hash but no
        # mined_hash — it must still be mined (missing marker -> re-mine).
        if (rows and rows[0] and rows[0][0] == file_hash
                and rows[0][1] == file_hash):
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
                             participants=ConversationMiner._extract_participants(text),
                             content_dedup=content_dedup,
                             dedup_threshold=dedup_threshold,
                             sdk=sdk)
            entities += res["entities"]
            objects += res["objects"]
            dedup_hits += res["dedup_hits"]
            drafts += res["drafts"]
        except Exception as e:
            errors.append({"file": rel, "error": str(e), "retryable": True})
            continue
        # Round-2 review: stamp a mined marker on the session Event (content
        # hash actually mined) so the next run's pre-scan can skip an
        # unchanged re-mine while still mining ingest-only sessions. The
        # marker is only stamped on SUCCESS — a failed/retried file keeps its
        # stale-or-absent marker and is re-mined next run. `MATCH` is a no-op
        # if ingest never created the session Event (e.g. lock-held skip).
        proj.g.query(
            "MATCH (e:Event {eventId:$eid}) SET e.mined_hash = $h, "
            "e.mined_at = $t",
            params={"eid": f"session_{session_id}",
                    "h": _hashlib.sha256(text.encode()).hexdigest(),
                    "t": _now()},
        )

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

# ── Phase-4 EP-safe batch commit gate (#785) ──────────────────────────

class EpSafeCommit:
    """W-3 EP-safe batch commit gate (plan §2 W-3, §4.5, §7 DE2E-8).

    Verifies an extraction batch BEFORE it enters the review queue:
      1. every extraction Point is `status: draft` (no SDK #131 edge
         auto-promotion leak for extraction paths)
      2. no operator auto-wire — batch operator nodes are not live AND every
         incident operator edge connects only draft endpoints (W-2/W-4
         draft-to-draft-only rule; live prior → review queue, never a draft→live
         edge)
      3. mean-grounding snapshot drift pre/post batch ≤ max_grounding_drift
         (Gate B tooling; #779 `mean_grounding`)

    Pass → batch COMMITTED (drafts enter the review queue; a previously
    quarantined batch is un-quarantined by the re-run pass — recovery loop,
    J-5). Fail → batch QUARANTINED (promote_point blocks on its Points until
    the cause is fixed and the batch re-runs clean).

    `proj` is a FalkorProjection (or anything exposing ``.g.query``) — the
    mining pipeline passes ``api.projection``; SDK callers pass
    ``sdk._get_proj()``. `grounding_fn` is the injectable mean-grounding seam:
    ``Callable[[], float]``; None resolves `tortoise.analyze.mean_grounding`
    (#779) and records the gate as unavailable/skipped until it lands.
    """

    def __init__(self, proj, batch_id: str, *,
                 max_grounding_drift: float = 0.02,
                 grounding_fn=None):
        self.proj = proj
        self.batch_id = batch_id
        self.max_grounding_drift = max_grounding_drift
        self._grounding_fn = grounding_fn if grounding_fn is not None \
            else _default_grounding_fn()

    # ── W-3 check 1: all extraction Points draft ───────────────────
    def verify_draft_only(self, point_ids: list[str]) -> list[dict]:
        """Return violations: batch Points that are not (explicitly) draft.

        Missing/unknown Points and any non-draft status (live/retracted/…) are
        violations — the batch must be 100% draft or it fails closed.
        """
        point_ids = list(point_ids)
        if not point_ids:
            return []
        rows = self.proj.g.query(
            "MATCH (n:Point) WHERE n.id IN $ids RETURN n.id, n.status",
            params={"ids": point_ids},
        ).result_set
        by_id = {row[0]: row[1] for row in rows}
        violations = []
        for pid in point_ids:
            status = by_id.get(pid)
            if pid not in by_id:
                violations.append({"id": pid, "reason": "not_found"})
            elif status != "draft":
                violations.append({"id": pid, "reason": "status_not_draft",
                                   "status": status})
        return violations

    # ── W-3 check 2: no operator auto-wire ─────────────────────────
    def verify_no_auto_wire(self, point_ids: list[str], *,
                            operator_ids: list[str] | None = None) -> list[dict]:
        """Return auto-wire violations for the batch's operator subgraph.

        Checks every incident operator node (given explicitly or discovered
        from the batch Points' edges): the operator node itself must not be
        live (extraction creates draft operator nodes — #780
        `create_operator(promote_source=False)`; on main the event path
        defaults operator nodes to live, which IS the R7 leak this catches),
        and every edge endpoint must be draft (draft-to-draft only).
        """
        point_ids = list(point_ids)
        if operator_ids is None:
            if not point_ids:
                return []
            rows = self.proj.g.query(
                "MATCH (o:Point {is_operator:true})-[r]->(n:Point) "
                "WHERE n.id IN $ids RETURN DISTINCT o.id",
                params={"ids": point_ids},
            ).result_set
            operator_ids = [row[0] for row in rows]
        violations = []
        for oid in operator_ids:
            op_rows = self.proj.g.query(
                "MATCH (o:Point {id:$id}) RETURN o.status",
                params={"id": oid},
            ).result_set
            if not op_rows:
                violations.append({"id": oid, "reason": "operator_not_found"})
                continue
            op_status = op_rows[0][0]
            # Canonical read model (tortoise/live.py): a missing raw status is
            # LIVE (projection coalesce default) — the pre-#780 create_operator
            # path writes no status, so the R7 leak manifests as raw NULL
            # (#944 review). Explicit 'draft' is the ONLY clean state.
            if (op_status or "live") == "live":
                violations.append({"id": oid, "reason": "operator_node_live",
                                   "status": op_status})
            # endpoints: every operator edge target must be draft
            eps = self.proj.g.query(
                "MATCH (o:Point {id:$oid})-[r]->(s:Point) "
                "RETURN s.id, s.status",
                params={"oid": oid},
            ).result_set
            for sid, st in eps:
                # missing status = implicit live (projection coalesce default)
                if (st or "live") != "draft":
                    violations.append({
                        "id": oid, "reason": "endpoint_live",
                        "endpoint_id": sid, "status": st,
                    })
        return violations

    # ── W-3 check 3: grounding snapshot drift ──────────────────────
    def _grounding_check(self, grounding_before: float | None,
                         grounding_after: float | None) -> dict:
        if self._grounding_fn is None:
            return {
                "status": "skipped",
                "note": "mean_grounding unavailable until #779 — gate skipped",
                "before": grounding_before, "after": grounding_after,
                "drift": None, "max_drift": self.max_grounding_drift,
            }
        if grounding_before is None:
            # Fail closed once the Gate B helper exists: a caller that skips
            # the pre-snapshot gets a hard gate, not a silent skip (#944
            # review). Pre-#779 the fn is None and this branch is unreachable.
            return {
                "status": "fail",
                "note": "grounding_before required when mean_grounding is available",
                "before": None, "after": grounding_after,
                "drift": None, "max_drift": self.max_grounding_drift,
            }
        try:
            after = grounding_after if grounding_after is not None \
                else self._grounding_fn()
            drift = abs(after - grounding_before)
        except Exception as exc:  # noqa: BLE001 — fail closed on runtime error
            return {
                "status": "fail",
                "note": f"grounding computation error: {exc}",
                "before": grounding_before, "after": grounding_after,
                "drift": None, "max_drift": self.max_grounding_drift,
            }
        ok = drift <= self.max_grounding_drift
        return {
            "status": "pass" if ok else "fail",
            "before": grounding_before, "after": after, "drift": drift,
            "max_drift": self.max_grounding_drift,
        }

    # ── W-3 gate ───────────────────────────────────────────────────
    def run(self, point_ids: list[str], *,
            operator_ids: list[str] | None = None,
            grounding_before: float | None = None,
            grounding_after: float | None = None) -> dict:
        """Run the full W-3 gate over a completed extraction batch.

        Returns {batch_id, ok, committed, quarantined, checks, reason}.
        Pass → :Batch status committed (un-quarantines on recovery re-run).
        Fail → quarantine_batch() with the first failing check's reason.
        """
        non_draft = self.verify_draft_only(point_ids)
        auto_wired = self.verify_no_auto_wire(point_ids, operator_ids=operator_ids)
        grounding = self._grounding_check(grounding_before, grounding_after)

        checks = {
            "all_points_draft": not non_draft,
            "non_draft_points": non_draft,
            "no_auto_wire": not auto_wired,
            "auto_wired": auto_wired,
            "grounding": grounding,
            "non_empty": len(point_ids) > 0,
        }
        failures = []
        if not point_ids:
            # Fail closed: an all-fail extraction produces zero Points and
            # must quarantine (plan J-1) — a vacuous pass would mark a
            # nonexistent batch committed (#944 review).
            failures.append("empty_batch")
        if non_draft:
            failures.append("points_not_draft")
        if auto_wired:
            failures.append("operator_auto_wire")
        if grounding.get("status") == "fail":
            failures.append("grounding_drift")

        prev = _batch_node(self.proj, self.batch_id)
        if failures:
            reason = (
                f"W-3 failed: {', '.join(failures)} "
                f"({len(point_ids)} points, batch {self.batch_id})"
            )
            quarantine_batch(self.proj, self.batch_id, reason=reason)
            return {
                "batch_id": self.batch_id,
                "ok": False,
                "committed": False,
                "quarantined": True,
                "reason": reason,
                "checks": checks,
            }

        recovered = (prev or {}).get("status") == BATCH_STATUS_QUARANTINED
        _upsert_batch(self.proj, self.batch_id, status=BATCH_STATUS_COMMITTED,
                      point_count=len(point_ids))
        return {
            "batch_id": self.batch_id,
            "ok": True,
            "committed": True,
            "quarantined": False,
            "recovered": recovered,
            "checks": checks,
        }
