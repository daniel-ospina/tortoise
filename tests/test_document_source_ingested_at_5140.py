"""#5140 (P2 finding 5) — a document `:Source` minted by the EVENT branch carries
no `ingestedAt`.

ONTOLOGY §4.6 marks `ingestedAt` **REQUIRED** on every `:Source` ("when Tortoise
first saw this source"). A document is a `:Source` (§4.4), and it is written by
three paths:

  * the `DocumentCreated` fold — `entities.py::_upsert_document`
    (`s.ingestedAt=coalesce(s.ingestedAt, $now)`);
  * the `objectType='Document'` event branch —
    `entities.py::_event_plain_merge` — **which wrote none**;
  * the hosted `/v1/sessions` transcript MERGE —
    `hosted_api.py::_execute_commit_writes` — **which wrote none**.

The two gaps are the same defect class #5082 closed for `sourceKind` on these
exact clauses: *"two writers of one node, disagreeing on a REQUIRED field"*. The
owners of the D10 requirement sweeps
(`MATCH (s:Source) WHERE … OR s.ingestedAt IS NULL …`) therefore read the
document the event branch minted as a violation.

Every test below names the input that makes it FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.projection import FalkorProjection

#: The instant the journal RECORD carries. `_emit` stamps every record with
#: `ts`, so this is the value the fold must read — not a replay clock.
RECORD_TS = "2026-01-02T03:04:05+00:00"


def _proj() -> FalkorProjection:
    """A throwaway embedded graph (no docker lane, no shared state)."""
    return FalkorProjection(
        os.path.join(tempfile.mkdtemp(prefix="tortoise_5140_"), "t.db"),
        graph_name="test",
    )


def _event(object_id: str, **record) -> dict:
    """The `EventRecorded` envelope `_upsert_event` accepts (nested form).

    ``ts`` rides the RECORD, exactly as `EventAPI._emit` writes it — the nested
    ``event`` payload never carries it.
    """
    return {
        "type": "EventRecorded",
        "ts": RECORD_TS,
        **record,
        "event": {
            "id": f"evt-{object_id}",
            "eventKind": "sessionCaptured",
            "subject": "agent-pi",
            "object": object_id,
            "objectType": "Document",
        },
    }


def _ingested_at(proj, url: str):
    rows = proj.g.query(
        "MATCH (s:Source {url:$u}) RETURN s.ingestedAt", params={"u": url},
    ).result_set
    return rows[0][0] if rows else None


def test_event_document_branch_stamps_ingested_at():
    """Input: an `EventRecorded` that produces a document `:Source`.

    FAILS IF the minted document `:Source` has no `ingestedAt` (§4.6 REQUIRED),
    or carries something other than the RECORD's own instant — the fold must
    read the recorded `ts`, so a replay produces the same value the live write
    did instead of re-stamping a replay clock (#5024's rule for `updatedAt`).
    """
    proj = _proj()
    try:
        proj.apply(_event("doc-5140-a"))
        assert _ingested_at(proj, "doc-5140-a") == RECORD_TS
    finally:
        proj.close()


def test_event_document_branch_fills_a_pre_fix_null_and_never_clobbers():
    """Input: a document `:Source` that already exists.

    FAILS IF the match arm leaves a pre-existing NULL `ingestedAt` NULL (the
    repair half #5082 added for `sourceKind`). The `doc-5140-stamped` assertion
    is NOT a falsifier of the pre-fix source — that writer never touched the
    field, so the 2020 value survived by ABSENCE, not by the new `coalesce`; it
    guards against a future re-stamp (the field means "when Tortoise FIRST saw
    this source" — #398's never-overwrite contract).
    """
    proj = _proj()
    try:
        for url in ("doc-5140-legacy", "doc-5140-stamped"):
            proj.g.query(
                "MERGE (s:Source {url:$u}) SET s.documentKind='transcript'",
                params={"u": url},
            )
        proj.g.query(
            "MATCH (s:Source {url:'doc-5140-stamped'}) "
            "SET s.ingestedAt='2020-05-06T07:08:09+00:00'")
        proj.apply(_event("doc-5140-legacy"))
        proj.apply(_event("doc-5140-stamped"))
        assert _ingested_at(proj, "doc-5140-legacy") == RECORD_TS
        assert _ingested_at(proj, "doc-5140-stamped") == "2020-05-06T07:08:09+00:00"
    finally:
        proj.close()


def test_a_corrupt_record_ts_falls_back_instead_of_being_silently_nulled():
    """Input: a journal line whose `ts` is not a driver-writable instant.

    FAILS IF the fold hands the RAW record value to the driver. The failure is
    SILENT, not a raise: `self.g`'s parameter boundary (`_journal_safe_params`)
    degrades an unwritable value to `None` rather than aborting the replay, and
    `None` in this DIRECT SET is a CLEAR — so the document `:Source` is created
    with NO `ingestedAt`, a §4.6-REQUIRED field, with only a WARNING logged.
    That is the D10 sweep red (#5140) this clause exists to close, which is why
    `_usable_instant` is the gate here and not the boundary. Mutation: restore
    `record_ts or _now_iso()` and this test fails on `got None`.
    """
    proj = _proj()
    try:
        proj.apply(_event("doc-5140-corrupt", ts={"evil": 1}))
        stamped = _ingested_at(proj, "doc-5140-corrupt")
        assert isinstance(stamped, str), (
            "a corrupt recorded `ts` must fall back to the write clock, and the "
            f"document :Source must still be stamped (§4.6 REQUIRED); got {stamped!r}"
        )
    finally:
        proj.close()


def test_the_d10_requirement_sweep_finds_no_violating_source():
    """Input: the §4.6 requirement query the D10 sweeps run.

    FAILS IF any `:Source` the event branch minted violates the sweep
    (`url`/`ingestedAt`/`sourceKind` all non-NULL). This is the exact query
    `#5140` used to reproduce the finding — it returned 1.
    """
    proj = _proj()
    try:
        proj.apply(_event("doc-5140-sweep"))
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.url IS NULL OR s.url='' "
            "   OR s.ingestedAt IS NULL OR s.sourceKind IS NULL "
            "RETURN s.url, s.ingestedAt, s.sourceKind",
        ).result_set
        assert rows == [], f"§4.6 REQUIRED-field violations: {rows!r}"
    finally:
        proj.close()


# ── #9419 — the SDK door that folds BEFORE it journals ───────────────────────
#
# #5140's fix made this field read the RECORD's own `ts` — which is
# deterministic only for the doors that STAMP that `ts` on the dict they fold.
# `TortoiseSDK._create_entity` (the funnel behind `create_event` /
# `create_entity`, and therefore the `tortoise_create_event` tool) folds first
# and journals after, so `_emit_event` minted the envelope `ts` a second time:
# the document `:Source` took the FOLD clock live and the APPEND clock on
# replay. Measured before the fix — live `…54.719525` vs rebuilt `…54.784866`.
#
# The tests below live in this file because they are the same field and the
# same fix shape; the door is the difference.


def _journaled_sdk():
    """A journal-backed SDK on a throwaway embedded graph (no docker lane)."""
    from tortoise.sdk import TortoiseSDK

    tmp = tempfile.mkdtemp(prefix="tortoise_9419_")
    events = os.path.join(tmp, "events")
    os.makedirs(events, exist_ok=True)
    log = os.path.join(events, "events.jsonl")
    return TortoiseSDK(os.path.join(tmp, "doc.db"), event_log_path=log), log


def test_create_entity_document_does_not_diverge_live_vs_rebuild():
    """Input: `create_event(objectType='Document')` on a journal-backed SDK.

    FAILS IF live and rebuilt disagree on the minted document `:Source`'s
    `ingestedAt`. That is #9419: the fold read no record `ts` (none existed
    yet), so it stamped `_now_iso()` at fold time while the append stamped the
    record's own instant a microsecond later — and a rebuild replays the
    record. Mutation: drop the `event["ts"] = _record_ts` stamp (or the
    `recorded_ts=` argument) and the two values differ again.
    """
    from tortoise.log import EventLog

    sdk, log = _journaled_sdk()
    try:
        sdk.create_event("evt-9419", "sessionCaptured", object="doc-9419.md",
                         objectType="Document")
        proj = sdk._get_proj()
        live = _ingested_at(proj, "doc-9419.md")
        assert live, "the document :Source must be stamped at all (§4.6 REQUIRED)"
        proj.rebuild_all(os.path.dirname(log), confirm_destructive=True)
        rebuilt = _ingested_at(proj, "doc-9419.md")
        assert live == rebuilt, (
            "`derived = replay(journal)` must hold for this field: the live "
            f"write stamped {live!r} and the rebuild {rebuilt!r}")
        # ...and the equality must come from the RECORD, not from two clocks
        # that happened to agree: the one instant is the envelope `ts`.
        rec = [e for e in EventLog(log).read_all()
               if e.get("type") == "EventRecorded"]
        assert len(rec) == 1, f"expected one EventRecorded line, got {len(rec)}"
        assert rec[0]["ts"] == live, (
            "the fold instant and the record's `ts` must be ONE clock read — "
            f"record ts {rec[0]['ts']!r} vs live ingestedAt {live!r}")
    finally:
        sdk.close()


def test_the_single_clock_stamp_never_becomes_an_event_property():
    """Input: the same create, read back off the `:Event` node.

    FAILS IF the envelope `ts` stamped for #9419 leaks onto the node. `ts` is a
    `_META_KEYS` member, so `_persist_extra_props` must skip it — and
    `_journal_record_payload` drops it from the payload, so a live-only `ts`
    property would be a NEW live/rebuild divergence, i.e. this fix rebuilding
    the defect it closes.
    """
    sdk, _log = _journaled_sdk()
    try:
        ev = sdk.create_event("evt-9419-ts", "sessionCaptured", object="doc-9419-ts.md",
                              objectType="Document")
        rows = sdk._get_proj().g.query(
            "MATCH (e:Event {eventId:$eid}) RETURN e.ts",
            params={"eid": ev["id"]},
        ).result_set
        assert len(rows) == 1, f"expected exactly one Event node, got {rows!r}"
        assert rows[0][0] is None, (
            f"envelope `ts` must never be a node property; got {rows!r}")
    finally:
        sdk.close()


# ── #9434 — the SDK door that APPENDS first, then folds the caller's dict ────
#
# #9419 took one of the two doors `_event_plain_merge`'s docstring names. This is
# the other, and its shape is why it was split out: `_apply_and_journal` appends
# through `_journal_entity_event` → `_emit_event`, which mints the envelope `ts`
# on a COPY of the record, and only THEN folds the CALLER's dict — which carries
# no `ts`, because `ts` is a reserved ENVELOPE prop. So the fold read
# `record_ts=None` and stamped `_now_iso()`, while a replay read the record's own
# `ts`: the #5024 `derived = replay(journal)` break, on `ingestedAt`.
#
# Same two tests as #9419 above, driven through the OTHER door.


def _applied_record(object_id: str) -> dict:
    """The FLAT `EventRecorded` record a caller of `_apply_and_journal` builds.

    Flat, not nested: `_upsert_event` unwraps `event.get("event", event)`, so the
    caller's own dict IS the payload — which is exactly why the append-first door
    could journal an instant the fold never saw.
    """
    return {
        "type": "EventRecorded",
        "id": f"evt-{object_id}",
        "eventKind": "sessionCaptured",
        "subject": "agent-pi",
        "object": object_id,
        "objectType": "Document",
    }


def test_apply_and_journal_document_does_not_diverge_live_vs_rebuild():
    """Input: a document `EventRecorded` through `_apply_and_journal`.

    FAILS IF live and rebuilt disagree on the minted document `:Source`'s
    `ingestedAt` — #9434. Pre-fix the fold read no record `ts` (the caller's dict
    had none) and stamped `_now_iso()` at fold time while the append stamped the
    record's own instant, so a rebuild replayed a DIFFERENT value than the live
    write stored. Mutation: drop the `event["ts"]` stamp in
    `_apply_and_journal` (or the `recorded_ts=` argument that hands it to
    `_journal_entity_event`) and the two values differ again.
    """
    from tortoise.log import EventLog

    sdk, log = _journaled_sdk()
    try:
        proj = sdk._get_proj()
        sdk._apply_and_journal(proj, _applied_record("doc-9434.md"))
        live = _ingested_at(proj, "doc-9434.md")
        assert live, "the document :Source must be stamped at all (§4.6 REQUIRED)"
        proj.rebuild_all(os.path.dirname(log), confirm_destructive=True)
        rebuilt = _ingested_at(proj, "doc-9434.md")
        assert live == rebuilt, (
            "`derived = replay(journal)` must hold for this field: the live "
            f"write stamped {live!r} and the rebuild {rebuilt!r}")
        # ...and the equality must come from the RECORD, not from two clocks
        # that happened to agree: the one instant is the envelope `ts`.
        rec = [e for e in EventLog(log).read_all()
               if e.get("type") == "EventRecorded"]
        assert len(rec) == 1, f"expected one EventRecorded line, got {len(rec)}"
        assert rec[0]["ts"] == live, (
            "the fold instant and the record's `ts` must be ONE clock read — "
            f"record ts {rec[0]['ts']!r} vs live ingestedAt {live!r}")
    finally:
        sdk.close()


def test_the_apply_and_journal_stamp_never_becomes_a_node_property():
    """Input: the same `_apply_and_journal` write, read back off BOTH nodes.

    FAILS IF the envelope `ts` stamped for #9434 leaks onto the folded nodes.
    This door is the one where a leak is most plausible, because the dict the
    fold receives is the CALLER's dict and the stamp mutates it in place. `ts` is
    a `_META_KEYS` member, so `_persist_extra_props` must skip it — and
    `_journal_record_payload` drops it from the payload, so a live-only `ts`
    property would be a NEW live/rebuild divergence, i.e. this fix rebuilding
    the defect it closes.
    """
    sdk, _log = _journaled_sdk()
    try:
        proj = sdk._get_proj()
        sdk._apply_and_journal(proj, _applied_record("doc-9434-ts.md"))
        for query, params, label in (
            ("MATCH (e:Event {eventId:$k}) RETURN properties(e)",
             {"k": "evt-doc-9434-ts.md"}, "Event"),
            ("MATCH (s:Source {url:$k}) RETURN properties(s)",
             {"k": "doc-9434-ts.md"}, "Source"),
        ):
            rows = proj.g.query(query, params=params).result_set
            assert len(rows) == 1, f"expected one {label} node, got {rows!r}"
            props = rows[0][0] or {}
            assert "ts" not in props, (
                f"envelope `ts` must never be a {label} node property; "
                f"got {sorted(props)!r}")
    finally:
        sdk.close()
