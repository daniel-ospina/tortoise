"""#2296 — a Document created through the SDK survives ``rebuild_all``.

The epic's own Indicator-2 gap list (scope hook from #2295) named this the
*last unjournaled canonical create* — that is the PRE-#2296 state, recorded as
closed in ``tests/test_unjournaled_mutation_class.py``. ``create_document`` / ``create_entity(type="document")`` route ``_create_entity("Document", <ulid>, …, "DocumentCreated")``
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

import inspect
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


def _forged_prop_value(name: str, forged_ts: str):
    """A caller value that WOULD forge the record if the name were not dropped."""
    if name in ("point", "payload"):
        return {"forged": True}
    if name == "type":
        return "SourceVersioned"  # would re-type/suppress the create record
    if name == "projection_version":
        return 99
    if name == "event_id":
        return "FORGED-EVENT-ID"
    if name == "initiated_by":
        return "FORGED-ACTOR"
    if name == "type_":
        return "X"
    if name == "new_content":
        return "FORGED"
    return forged_ts  # ts / recorded_ts


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

    NOT COVERED HERE: an envelope key the caller also supplies as a ``props``
    key. Those no longer reach this record at all — ``_RESERVED_ENVELOPE_PROPS``
    (every ``_JOURNAL_ENVELOPE_KEYS`` name but ``id``) is stripped at prop
    INTAKE by ``_sanitize_props``, and ``create_source`` — which bypasses that
    intake — drops the same set plus the reserved actor keys, so the caller's
    value reaches neither the live node nor the record. That refusal, and the
    ``type``-suppression path it closes, are asserted in
    ``test_a_reserved_envelope_prop_cannot_forge_or_suppress_the_record``.
    ``id`` is the one envelope name not in that set: it keeps its own
    fail-closed ``ValueError`` contract instead.
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


def test_a_reserved_envelope_prop_cannot_forge_or_suppress_the_record(env):
    """An ``_emit_event``-owned name must never reach it as a caller prop.

    Two families are reserved, and this test drives BOTH through the doors that
    can carry a caller prop:

    * ``_RESERVED_ENVELOPE_PROPS`` — the journal ENVELOPE names (measured on
      ``update_point``: a caller ``recorded_ts`` forges the record's ``ts``,
      ``point`` hands the record the node snapshot a rebuild then replays,
      ``ts``/``event_id``/``projection_version`` overwrite the built envelope
      via ``event.update(extra)``).
    * ``_RESERVED_EMIT_CALL_PROPS`` — names that are NOT envelope keys but
      collide at the CALL: ``type_`` is `_emit_event`'s first parameter and
      ``new_content`` is passed explicitly by ``update_point``'s emit, so
      either raised ``TypeError`` AFTER the live write had landed — the
      mutation stayed live-only and was destroyed by ``rebuild_all``.

    ``type`` is the key the live fold and the record BOTH dispatch on, so a prop
    of that name re-types or silently SUPPRESSES the record: measured,
    ``create_document(type="SourceCreated")`` journalled nothing and
    ``rebuild_all`` destroyed the node — the #2296 defect itself.

    Both sets are stripped at prop intake (``_sanitize_props``), the one surface
    ``create_point``/``update_point``/``create_entity``/``update_entity`` all
    pass through; ``create_source`` bypasses that intake and drops both sets —
    plus the reserved actor keys — itself, and that lane is driven here for
    EVERY reserved name, because a name that survives it raises inside a
    swallowed ``try/except`` and produces no record at all.

    FAILS IF ``_RESERVED_ENVELOPE_PROPS`` drifts from ``_JOURNAL_ENVELOPE_KEYS``
    minus ``id``, IF the two reserved sets overlap, IF any name is left out of
    the intake strip, or IF any name is left out of ``create_source``'s drop."""
    sdk, events = env
    forged = "1999-01-01T00:00:00+00:00"
    from tortoise.sdk import (
        _JOURNAL_ENVELOPE_KEYS,
        _RESERVED_EMIT_CALL_PROPS,
        _RESERVED_ENVELOPE_PROPS,
        _journal_record_payload,
    )
    # The invariant the change rests on, asserted rather than promised in a
    # comment: the reserved set IS the envelope vocabulary minus `id`, and the
    # two sets never overlap (a call name belongs in exactly one place). A name
    # ADDED to `_JOURNAL_ENVELOPE_KEYS` without a reservation is the drift this
    # test exists to catch — the loop below cannot see it, because it iterates
    # the reserved set itself.
    assert set(_RESERVED_ENVELOPE_PROPS) == set(_JOURNAL_ENVELOPE_KEYS) - {"id"}, (
        "`_RESERVED_ENVELOPE_PROPS` must equal `_JOURNAL_ENVELOPE_KEYS` minus "
        "`id`: an envelope name reserved in one place and not the other "
        "re-opens the caller-prop collision this test closes")
    assert not (set(_RESERVED_EMIT_CALL_PROPS) & set(_JOURNAL_ENVELOPE_KEYS)), (
        "`_RESERVED_EMIT_CALL_PROPS` is for names that are NOT envelope keys; "
        "an envelope key belongs in `_RESERVED_ENVELOPE_PROPS`")
    _all_reserved = (*_RESERVED_ENVELOPE_PROPS, *_RESERVED_EMIT_CALL_PROPS)
    # Every parameter `_emit_event` owns is a name a caller prop can bind at the
    # call, so the reserved sets must COVER its signature — DERIVED here rather
    # than hand-listed, because the hand-listed set drifted and left `self` open
    # (measured: `create_source(url, "document", props={"self": …})` raised
    # inside `_emit_event`, that door's journal try/except swallowed it, and
    # `rebuild_all` destroyed the live-only `:Source`). `**extra` is the
    # documented passthrough, and `id` is deliberately excluded (the entity
    # doors reject it fail-closed; `create_point` consumes it as its explicit-id
    # path).
    _owned = {n for n, p in inspect.signature(
        TortoiseSDK._emit_event).parameters.items()
        if p.kind is not inspect.Parameter.VAR_KEYWORD} - {"id"}
    assert not (_owned - set(_all_reserved)), (
        f"`_emit_event` owns {sorted(_owned - set(_all_reserved))!r} but no "
        "reserved set covers them: a caller prop of such a name binds that "
        "parameter at the call — after the live write — and `_emit_event` "
        "cannot see the collision from inside")
    # The record side: every ENVELOPE name must be stripped from EVERY
    # entity-door payload, not only from the one call this test happens to
    # drive. (`_RESERVED_EMIT_CALL_PROPS` are not envelope keys, so
    # `_journal_record_payload` is not the seam that strips those — the intake
    # strip below is.)
    for name in _RESERVED_ENVELOPE_PROPS:
        assert name not in _journal_record_payload(
            {"type": "DocumentCreated", "id": "x", name: forged}), (
            f"{name!r} is an `_emit_event` envelope name and must be stripped "
            "from every entity-door record payload — leaving it in lets a "
            "caller prop bind that parameter or re-type the record")
    doc = sdk.create_document("Forged", "report", recorded_ts=forged)
    rec = _document_created(events)[0]
    assert rec["ts"] != forged, (
        "a caller `recorded_ts` prop forged the record's envelope ts")
    live = _sources(sdk._get_proj(), doc["id"])[0][0]
    assert "recorded_ts" not in live, (
        "a caller `recorded_ts` prop persisted live while the record strips "
        "it — the #8553 live != replay divergence")
    # The Source door emits its OWN record (`SourceCreated`), bypasses the
    # intake strip, and drops the reserved names itself — including the ACTOR
    # keys, which the sanitizer would otherwise have taken.
    sdk.create_source("https://example.com/rt", "document",
                      recorded_ts=forged, initiated_by="FORGED-ACTOR",
                      summary="rt")
    src_rec = next(e for e in _journal(events)
                   if e.get("type") == "SourceCreated")
    assert src_rec["ts"] != forged, (
        "a caller `recorded_ts` prop forged the SourceCreated record's ts")
    assert src_rec.get("initiated_by") == "sdk", (
        "a caller `initiated_by` prop forged the SourceCreated record's actor")
    # The generic update door splats caller props straight into `_emit_event`,
    # so before the intake strip each of these bound its own parameter or
    # overwrote the built envelope via `event.update(extra)`.
    pt = sdk.create_point("statement", "a point")
    # `type_` and `new_content` are the CALL-collision names: pre-fix either of
    # them in this splat raises `TypeError` AFTER the live write, with no
    # `PointRevised` record (so this call itself errors before any assertion).
    sdk.update_point(pt["id"], recorded_ts=forged, type="PointAdded",
                     point={"id": "forged"}, payload={"forged": True},
                     ts=forged, event_id="FORGED-EVENT-ID",
                     projection_version=99, initiated_by="FORGED-ACTOR",
                     type_="X", new_content="FORGED", summary="rt")
    rev = next(e for e in _journal(events)
               if e.get("type") == "PointRevised")
    assert rev["ts"] != forged, (
        "a caller `recorded_ts`/`ts` prop forged the record's envelope ts")
    # `point` is the non-vacuous half: it is an `_emit_event` PARAMETER, so a
    # surviving prop would put the caller's snapshot on the record. `payload`
    # is deliberately NOT asserted — it is also a parameter, so it can never
    # appear as a record key and `.get("payload")` could not fail on any
    # version (an assertion that reads as a pin but is a tautology).
    assert rev.get("point") is None, (
        "a caller `point` prop became the record's snapshot — the rebuild "
        "replays it onto the node")
    assert (rev.get("event_id") != "FORGED-EVENT-ID"
            and rev.get("projection_version") == 2
            and rev.get("initiated_by") == "sdk"), (
        "a caller prop overwrote a built envelope field via "
        "`event.update(extra)`")
    pt_live = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN properties(n)",
        params={"id": pt["id"]}).result_set[0][0]
    for name in _all_reserved:
        assert name not in pt_live, (
            f"a caller {name!r} prop persisted live on the Point while the "
            "record strips it — the #8553 live != replay divergence")
    # A `type` prop must not suppress a Document's record: with it suppressed
    # the node is live-only and `rebuild_all` destroys it.
    n_before = len(_document_created(events))
    doc2 = sdk.create_document("Forged2", "report", type="SourceCreated")
    recs = _document_created(events)
    # Two SEPARATE assertions: `recs` is `_document_created(events)`, already
    # filtered to `type == "DocumentCreated"`, so a `recs[-1].get("type") ==
    # ...` conjunct inside an `and` could never fail. The id match is the real
    # second pin.
    assert len(recs) == n_before + 1, (
        "a caller `type` prop re-typed or suppressed the DocumentCreated "
        f"record: journal={[e.get('type') for e in _journal(events)]}")
    assert recs[-1].get("id") == doc2["id"], (
        "the new DocumentCreated record does not belong to the document whose "
        "create passed the `type` prop")
    # The `_skip_sanitize` door (`create_source`) drops BOTH sets itself. Drive
    # EVERY reserved name through it: a name that survives makes the journal
    # emit raise inside create_source's swallowed `try/except`, so the caller
    # gets a success response, NO record is written, and the `:Source` is
    # live-only — destroyed by the next rebuild (#2296).
    #
    # `payload` is the one name TWO contracts claim, and the LOUDER one wins:
    # D30/#3998 refuses that spelling on a `:Source` BEFORE the reserved strip
    # runs, because it is also the common raw-body mistake that guard exists to
    # answer with an actionable error rather than a silent drop. That refusal
    # is correct, so it is asserted separately here — driving it through the
    # strip loop below would assert the wrong contract (the record is never
    # written because the call raises, not because the name was dropped).
    _loud_d30 = {"payload"}
    for name in sorted(_loud_d30 & set(_all_reserved)):
        with pytest.raises(ValueError, match="raw payload"):
            sdk.create_source(f"https://example.com/d30-{name}", "document",
                              **{name: _forged_prop_value(name, forged)})
    _bypass: dict[str, str] = {}
    for i, name in enumerate(n for n in _all_reserved if n not in _loud_d30):
        url = f"https://example.com/bypass-{i}"
        _value = _forged_prop_value(name, forged)
        # `self` cannot be passed as a direct kwarg — Python rejects it at THIS
        # call — but it is reachable exactly the way a caller reaches it:
        # through the documented nested-`props` convention (`_coerce_props`),
        # which is flattened before the names are splatted into `_emit_event`.
        _kw = {"props": {name: _value}} if name == "self" else {name: _value}
        sdk.create_source(url, "document", **_kw)
        _bypass[url] = name
    _src = {e.get("id"): e for e in _journal(events)
            if e.get("type") == "SourceCreated"}
    for url, name in _bypass.items():
        src = _src.get(url)
        assert src is not None, (
            f"`create_source` wrote no `SourceCreated` record for a caller "
            f"{name!r} prop: it survived the door's strip, the emit raised "
            f"inside the swallowed try/except, and the :Source is live-only "
            f"(#2296)")
        assert (src["ts"] != forged
                and src["event_id"] != "FORGED-EVENT-ID"
                and src["projection_version"] == 2
                and src.get("initiated_by") == "sdk"
                and src.get("point") is None
                and src.get("new_content") is None), (
            f"a caller {name!r} prop was not dropped by the create_source "
            f"door's strip: {src!r}")
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    for _id in (doc["id"], doc2["id"], *_bypass):
        assert _sources(sdk._get_proj(), _id), (
            "a Document/Source was live-only: `rebuild_all` destroyed it "
            "(#2296)")


def test_update_entity_source_refuses_reserved_props_loudly(env):
    """The intake strip must not silence the #3998/D30 ``:Source`` refusal.

    ``_update_entity`` strips the reserved names at intake, BEFORE its
    target-aware ``:Source`` guard. On a ``:Source`` an undeclared key is the
    COMMON raw-body mistake that guard exists to answer LOUDLY and
    key-naming — and a `props` bag of ONLY reserved names would otherwise
    leave nothing for the guard's ``if props:`` gate and return a
    success-shaped no-op with the write discarded.

    RED without the pre-strip capture: ``update_entity(<Source>, payload=...)``
    returned normally (measured at the pre-fix head).

    ``event_id`` is deliberately EXCLUDED from that loop: it is both a reserved
    envelope name and a DECLARED `:Source` property, so the reservation drops it
    while the update must still LAND — see the assertions below.
    """
    sdk, _events = env
    url = "https://example.com/refuse"
    sdk.create_source(url, "document")
    # UNDECLARED reserved names: the loud D30 refusal still fires even though the
    # intake strip already removed them from `props` — the pre-strip capture is
    # what keeps the guard answerable.
    for name in ("payload", "point", "type", "ts",
                 "projection_version", "type_", "new_content"):
        with pytest.raises(ValueError, match="cannot be set on a :Source"):
            sdk.update_entity(url, **{name: {"k": "v"}})
    # `event_id` is the one reserved name that is ALSO declared on a `:Source`,
    # so it must not be pushed into the guard's undeclared set: measured, the
    # unsubtracted union refused the WHOLE update — the legitimate co-supplied
    # `summary` was lost — with a message whose own "Declared :Source
    # properties" list names `event_id` as settable. The update must land, and
    # the name must not persist (the envelope owns it; a live-only `event_id`
    # would be the #8553 live != replay divergence).
    sdk.update_entity(url, event_id="FORGED-EVENT-ID", summary="kept")
    live = sdk._get_proj().g.query(
        "MATCH (s:Source {url:$u}) RETURN s.summary, s.event_id",
        params={"u": url}).result_set[0]
    assert live[0] == "kept", (
        "a declared reserved prop (`event_id`) refused the whole update: the "
        "legitimate co-supplied `summary` was discarded")
    assert live[1] is None, (
        "a caller `event_id` prop persisted live on the :Source while the "
        "record strips it — the #8553 live != replay divergence")
    # The reserved ACTOR keys keep their strip-and-ignore contract (#2600):
    # never a 4xx — the server owns attribution.
    sdk.update_entity(url, initiated_by="FORGED-ACTOR")


def test_a_relay_door_strips_the_reserved_names_before_splatting(env):
    """A door that SPLATS the caller bag onward must strip BEFORE the relay.

    ``update_entity`` relays to ``_update_entity``, and ``create_subject`` /
    ``create_object`` / ``create_event`` relay to ``create_entity`` — every one of
    those callees takes ``self`` as its FIRST parameter and ``type`` as its
    next, so a ``self``/``type`` key (reachable through the documented nested
    ``props={...}`` convention) raised ``TypeError`` AT THE RELAY, one frame
    before the intake strip could drop it. Measured before the fix:
    ``create_subject(name, props={"self": …})`` raised ``TypeError``, and the
    ingest bundle's Phase-2 relay aborted AFTER an earlier section had already
    committed.

    FAILS IF a relay forwards a reserved name un-stripped — the call raises
    instead of dropping the name — or if the D30 ``:Source`` refusal stops
    answering an undeclared reserved name that arrives this way.
    """
    sdk, _events = env
    poisoned = {"self": "FORGED", "type": "FORGED", "type_": "X",
                "new_content": "FORGED", "point": {"forged": True}}
    for door, args in ((sdk.create_subject, ("relay-subject",)),
                       (sdk.create_object, ("relay-object",)),
                       (sdk.create_event, ("relay-event", "meeting"))):
        node = door(*args, props=dict(poisoned))
        assert node.get("id"), f"{door.__name__} relay dropped the write"
        live = sdk._get_proj().g.query(
            "MATCH (n {id:$i}) RETURN properties(n)",
            params={"i": node["id"]}).result_set[0][0]
        for name in poisoned:
            assert name not in live, (
                f"{door.__name__}: a reserved {name!r} prop reached the node "
                "through the relay splat")
    # `update_entity` relays to `_update_entity` (same `self` hazard). On a
    # POINT the reserved names are strip-and-ignore, so the legitimate co-supplied
    # `summary` must land; on a `:Source` the same undeclared name keeps the LOUD
    # D30 refusal it has through every other path.
    pt = sdk.create_point("statement", "relay point")
    sdk.update_entity(pt["id"], props={"self": "FORGED", "summary": "relayed"})
    pt_live = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$i}) RETURN properties(n)",
        params={"i": pt["id"]}).result_set[0][0]
    assert pt_live.get("summary") == "relayed" and "self" not in pt_live, (
        f"the update_entity relay mishandled a reserved prop: {pt_live!r}")
    url = "https://example.com/relay-source"
    sdk.create_source(url, "document")
    with pytest.raises(ValueError, match="cannot be set on a :Source"):
        sdk.update_entity(url, props={"self": "FORGED"})


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
