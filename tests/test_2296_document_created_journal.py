"""#2296 — a Document created through the SDK survives ``rebuild_all``.

The epic's own Indicator-2 gap list (scope hook from #2295) and
``tests/test_unjournaled_mutation_class.py`` both name this as the *last
unjournaled canonical create*: ``create_document`` / ``create_entity(type=
"document")`` route ``_create_entity("Document", <ulid>, …, "DocumentCreated")``
and emitted **no journal record** on that route. ``DocumentCreated`` is a
fold the replay engines honour (``_upsert_document``), but with no record to
replay a journal-enabled SDK's Documents were live-only — every
``rebuild_all`` destroyed them, silently.

The fix appends ``DocumentCreated`` from ``_create_entity``'s Document arm
(the funnel, not either call site), before the fold, on EVERY write — there
is no first-registration probe to gate on (#2194/#2295's design does not
port: the id is a fresh ulid and ``_upsert_document`` MERGEs by id, so a
re-mention cannot collide). ``create_source`` is NOT the same contract: its
cadence is per-OUTCOME since #5024 (T6) — ``SourceCreated`` on create /
stub-completion, ``SourceVersioned`` on a real hash transition, nothing on a
provable no-op re-check — and this door deliberately differs (its id is fresh
per call, so it can never address an existing node).

RED before the fix: ``test_create_document_journals_and_survives_rebuild``
and ``test_create_entity_document_journals_and_survives_rebuild`` (the
``:Source`` is gone after ``rebuild_all``) and the journal assertions
(``DocumentCreated`` absent from ``events.jsonl``).
"""
from __future__ import annotations

import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def env(tmp_path):
    """(sdk, events_dir) with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "doc.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _journal(events) -> list[dict]:
    return EventLog(str(events / "events.jsonl")).read_all()


def _document_created(events) -> list[dict]:
    return [e for e in _journal(events) if e.get("type") == "DocumentCreated"]


def _sources(proj, url: str):
    return proj.g.query("MATCH (s:Source {url:$u}) RETURN properties(s)",
                        params={"u": url}).result_set


def test_create_document_journals_and_survives_rebuild(env):
    sdk, events = env
    doc = sdk.create_document("Quarterly Report", "report")
    did = doc["id"]
    proj = sdk._get_proj()

    created = _document_created(events)
    assert len(created) == 1, (
        "create_document emitted no DocumentCreated record — the Document is "
        f"live-only and a rebuild will destroy it (#2296): journal="
        f"{[e.get('type') for e in _journal(events)]}")
    assert created[0]["id"] == did

    assert _sources(proj, did), "sanity: the Document :Source exists live"
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = _sources(proj, did)
    assert rows, "rebuild_all destroyed the Document (#2296)"
    props = rows[0][0]
    assert props.get("title") == "Quarterly Report"
    assert props.get("documentKind") == "report"


def test_create_entity_document_journals_and_survives_rebuild(env):
    sdk, events = env
    node = sdk.create_entity("document", "Design Notes", documentKind="brief")
    did = node["node"]["id"]
    proj = sdk._get_proj()

    created = _document_created(events)
    assert len(created) == 1, (
        "create_entity(type='document') emitted no DocumentCreated record "
        f"(#2296): journal={[e.get('type') for e in _journal(events)]}")

    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = _sources(proj, did)
    assert rows, "rebuild_all destroyed the document :Source (#2296)"
    assert rows[0][0].get("documentKind") == "brief"


def test_the_record_carries_the_applied_payload(env):
    """The journal line reproduces the applied payload — NOT byte for byte.

    ``_journal_record_payload`` drops the envelope keys ``_emit_event`` owns,
    and ``_upsert_document`` re-merges the record onto a FRESH node at replay.
    For this create (no ``props``, so no caller key collides with an envelope
    key) exactly three properties of the LIVE node are NOT reproduced, and the
    rebuild below asserts that EXACT set:

    * ``eventId`` — the live create carries no ``event_id``, so the fold
      coalesces it to ``""``; replay reads the record's ``event_id`` envelope
      key, so the rebuilt node carries the journal line's ulid.
    * ``ingestedAt`` / ``updatedAt`` — re-stamped at rebuild time (the fold's
      own clock), so they differ from the live values by the rebuild delay.

    Everything else — ``title``, ``documentKind``, ``url``, the embedding, the
    derived ``_searchText`` — IS reproduced. The assertion is the FULL property
    map (not just the three payload keys), so a NEW unintended divergence
    fails here instead of passing silently.

    NOT COVERED HERE: a caller ``props`` key that collides with an envelope
    key (``event_id``, ``ts``, ``point``, …) reaches the live node but is
    stripped from the record by ``_journal_record_payload`` (or shadowed by
    the envelope value), so live ≠ replay for that key. That is the
    pre-existing envelope-key class — also reachable via ``create_source``'s
    ``**props`` splat — not introduced by #2296, tracked as #8553, and not
    asserted here. ``recorded_ts`` is EXCLUDED by the code rather than left to
    #8553: it is the name of an ``_emit_event`` PARAMETER, so a caller prop of
    it would bind that parameter and forge the record's envelope ``ts``; it is
    in ``_JOURNAL_ENVELOPE_KEYS`` and popped from the live event in
    ``_create_entity``.
    """
    sdk, events = env
    doc = sdk.create_document("Payload Doc", "report")
    proj = sdk._get_proj()
    live = _sources(proj, doc["id"])[0][0]
    rec = _document_created(events)[0]

    # The applied payload the record owns (envelope keys excluded) lands on the
    # live node verbatim...
    assert rec["title"] == live["title"]
    assert rec["document_kind"] == live["documentKind"]
    assert rec["url"] == doc["id"]

    proj.rebuild_all(str(events), confirm_destructive=True)
    after = _sources(proj, doc["id"])[0][0]

    # ...and the rebuild reproduces everything EXCEPT the three named
    # properties. Report the differing KEYS, not value reprs — two long
    # property maps are unreadable and this assertion has to be actionable.
    diverged = sorted(k for k in set(live) | set(after)
                      if live.get(k) != after.get(k))
    assert diverged == ["eventId", "ingestedAt", "updatedAt"], (
        "rebuild_all diverged from live beyond the documented "
        f"eventId/ingestedAt/updatedAt set: {diverged}")

    # Pin the DIRECTION of each, so an inversion (e.g. the payload stops being
    # applied, or the eventId divergence reverses) is caught rather than only
    # the key set.
    assert live["eventId"] == "" and after["eventId"] == rec["event_id"], (
        "the rebuilt node's eventId is the journal line's envelope id, and the "
        f"live create leaves it empty: live={live['eventId']!r} "
        f"after={after['eventId']!r} rec.event_id={rec['event_id']!r}")
    assert after["ingestedAt"] != live["ingestedAt"], (
        "ingestedAt is re-stamped at rebuild time — the live value must not "
        "survive the replay")
    assert after["updatedAt"] != live["updatedAt"], (
        "updatedAt is re-stamped at rebuild time — the live value must not "
        "survive the replay")


def test_a_recorded_ts_prop_cannot_forge_the_envelope(env):
    """A caller ``recorded_ts`` prop is an ``_emit_event`` PARAMETER name, not
    a domain prop. It must not bind the envelope ``ts`` (the record's ts is
    minted by ``_emit_event``), and it must not persist live either — the
    record strips it, so keeping it live would be the #8553 live ≠ replay
    divergence. Every emitter that can turn the prop into an envelope ``ts`` is
    checked: ``create_document`` (via ``_create_entity``'s live pop),
    ``create_source`` (which emits its own ``SourceCreated`` and needs its own
    drop), and ``update_point`` (which splats caller props straight into
    ``_emit_event`` — the shared intake strip in ``_sanitize_props`` is what
    covers it). FAILS IF ``recorded_ts`` leaves ``_JOURNAL_ENVELOPE_KEYS`` (the
    record's ``ts`` becomes caller-controlled), or the intake strip / either
    door's live drop is removed."""
    sdk, events = env
    forged = "1999-01-01T00:00:00+00:00"
    # The record side: `recorded_ts` is an `_emit_event` PARAMETER name, so it
    # must be stripped from EVERY entity-door payload (the tuple entry), not
    # only from the one call this test happens to drive.
    from tortoise.sdk import _journal_record_payload
    assert "recorded_ts" not in _journal_record_payload(
        {"type": "DocumentCreated", "id": "x", "recorded_ts": forged}), (
        "`recorded_ts` is an `_emit_event` parameter name and must be stripped "
        "from every entity-door record payload — leaving it in lets a caller "
        "prop bind the `recorded_ts` parameter and forge the envelope `ts`")
    doc = sdk.create_document("Forged", "report", recorded_ts=forged)
    rec = _document_created(events)[0]
    assert rec["ts"] != forged, (
        "a caller `recorded_ts` prop forged the record's envelope ts")
    live = _sources(sdk._get_proj(), doc["id"])[0][0]
    assert "recorded_ts" not in live, (
        "a caller `recorded_ts` prop persisted live while the record strips "
        "it — the #8553 live != replay divergence")
    # The Source door emits its OWN record (`SourceCreated`) and does not route
    # through `_journal_entity_event`, so it needs its own drop.
    sdk.create_source("https://example.com/rt", "document",
                      recorded_ts=forged, summary="rt")
    src_rec = next(e for e in _journal(events)
                   if e.get("type") == "SourceCreated")
    assert src_rec["ts"] != forged, (
        "a caller `recorded_ts` prop forged the SourceCreated record's ts")
    # The generic update door: `update_point` splats caller props into
    # `_emit_event` directly, so before the intake strip a prop of this name
    # bound the `recorded_ts` PARAMETER and forged the `PointRevised` record's
    # `ts` (measured). `_sanitize_props` is the shared intake it passes through.
    pt = sdk.create_point("note", "a point")
    sdk.update_point(pt["id"], recorded_ts=forged, summary="rt")
    rev = next(e for e in _journal(events)
               if e.get("type") == "PointRevised")
    assert rev["ts"] != forged, (
        "a caller `recorded_ts` prop forged the PointRevised record's ts")
    pt_live = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pt["id"]}).result_set[0][0]
    assert "recorded_ts" not in pt_live, (
        "a caller `recorded_ts` prop persisted live on the Point while the "
        "record strips it — the #8553 live != replay divergence")


def test_document_mutation_survives_rebuild(env):
    """The creation fix also makes a later document MUTATION durable.

    Pre-fix the node was destroyed by the rebuild, so the mutation could not
    survive it. The mutation is addressed by the document's canonical
    ``:Source`` label (a document IS a ``:Source``, D10), which is in
    ``_CANONICAL_ENTITY_LABELS``, so it folds.
    """
    sdk, events = env
    doc = sdk.create_document("MutDoc", "report")
    sdk.update_entity(doc["id"], documentKind="brief")
    proj = sdk._get_proj()
    proj.rebuild_all(str(events), confirm_destructive=True)
    rows = _sources(proj, doc["id"])
    assert rows, "rebuild_all destroyed the Document (#2296)"
    assert rows[0][0].get("documentKind") == "brief"


def test_one_create_is_one_record(env):
    """Emit-on-every-write must not become emit-twice-per-write."""
    sdk, events = env
    sdk.create_document("A", "report")
    sdk.create_document("B", "report")
    assert len(_document_created(events)) == 2


def test_journal_less_sdk_still_creates_the_document(tmp_path):
    """A journal-less SDK still creates the Document — the fix must not require
    a journal. This is the deliberate CONTROL in the pre-fix mutation pass: the
    other seven tests redden when ``DocumentCreated`` is unjournaled, this one
    stays green because the live create is independent of the record."""
    sdk = TortoiseSDK(str(tmp_path / "nojournal.db"))
    try:
        doc = sdk.create_document("No Journal", "report")
        assert _sources(sdk._get_proj(), doc["id"]), (
            "an unconfigured journal must not change the create")
    finally:
        sdk.close()


def test_append_failure_does_not_crash_the_create(env, monkeypatch, caplog):
    """Mirror the pinned Object behaviour: append failure is fail-soft.

    The append precedes the fold, but — like `_create_entity`'s
    Object/Subject/Event blocks (`test_object_registered_journal.py::
    test_log_append_failure_warns_and_keeps_live`) — it must not crash the
    create; the node stays live and the loss is logged.
    """
    sdk, _events = env
    proj = sdk._get_proj()
    orig = EventLog.append

    def _raise_append(self, event):
        raise OSError("disk full (test)")

    monkeypatch.setattr(EventLog, "append", _raise_append)
    try:
        with caplog.at_level(logging.ERROR, logger="tortoise.sdk"):
            doc = sdk.create_document("Append Fail", "report")
        assert _sources(proj, doc["id"]), (
            "an append failure must not crash the create; node stays live")
        assert any("failed to append" in r.getMessage()
                   for r in caplog.records), caplog.records
    finally:
        monkeypatch.setattr(EventLog, "append", orig)
