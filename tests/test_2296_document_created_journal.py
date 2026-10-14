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
    and ``_upsert_document`` re-merges the record onto a FRESH node at replay,
    so three properties of the LIVE node are NOT reproduced. The rebuild below
    asserts that EXACT set:

    * ``eventId`` — the live create carries no ``event_id``, so the fold
      coalesces it to ``""``; replay reads the record's ``event_id`` envelope
      key, so the rebuilt node carries the journal line's ulid.
    * ``ingestedAt`` / ``updatedAt`` — re-stamped at rebuild time (the fold's
      own clock), so they differ from the live values by the rebuild delay.

    Everything else — ``title``, ``documentKind``, ``url``, the embedding, the
    derived ``_searchText`` — IS reproduced. The assertion is the FULL property
    map (not just the three payload keys), so a NEW unintended divergence
    fails here instead of passing silently.
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


def test_journal_less_sdk_is_byte_identical(tmp_path):
    """A journal-less SDK persists the Document exactly as before (no crash)."""
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
