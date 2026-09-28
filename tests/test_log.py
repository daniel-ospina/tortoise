"""EventLog tests — append-only JSONL store.

Runnable without pytest:  .venv/bin/python tests/test_log.py
(also works under pytest if installed).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.log import (  # noqa: E402, RUF100
    TORN_TAIL_HARMLESS_EVENT_TYPES,
    EventLog,
    describe_torn_tail_revival,
    torn_record_may_revive_state,
)


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def test_init_string_path():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    assert isinstance(log.path, Path)
    assert str(log.path) == p
    print("PASS test_init_string_path")


def test_init_path_object():
    p = Path(_tmp("events.jsonl"))
    log = EventLog(p)
    assert isinstance(log.path, Path)
    assert log.path == p
    print("PASS test_init_path_object")


def test_append_writes_valid_jsonl():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"type": "PointAdded", "content": "hello"})
    with open(p, "r", encoding="utf-8") as f:  # noqa: UP015
        line = f.readline().strip()
    parsed = json.loads(line)
    assert parsed == {"type": "PointAdded", "content": "hello"}
    print("PASS test_append_writes_valid_jsonl")


def test_multiple_appends_multiple_lines():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    for i in range(3):
        log.append({"n": i})
    with open(p, "r", encoding="utf-8") as f:  # noqa: UP015
        lines = [l.strip() for l in f if l.strip()]  # noqa: E741
    assert len(lines) == 3
    assert [json.loads(l)["n"] for l in lines] == [0, 1, 2]  # noqa: E741
    print("PASS test_multiple_appends_multiple_lines")


def test_read_all_nonexistent_file():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    assert not os.path.exists(p)
    assert log.read_all() == []
    print("PASS test_read_all_nonexistent_file")


def test_read_all_empty_file():
    p = _tmp("events.jsonl")
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    Path(p).touch()
    log = EventLog(p)
    assert log.read_all() == []
    print("PASS test_read_all_empty_file")


def test_read_all_returns_dicts():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"x": 1})
    log.append({"x": 2})
    result = log.read_all()
    assert result == [{"x": 1}, {"x": 2}]
    print("PASS test_read_all_returns_dicts")


def test_append_none_writes_null():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append(None)  # None → json null — a valid JSON scalar, not an error
    result = log.read_all()
    assert result == [None]
    print("PASS test_append_none_writes_null")


def test_append_non_serializable_raises():
    log = EventLog(_tmp("events.jsonl"))
    try:
        log.append({"bad": {1, 2, 3}})  # set is not JSON-serializable
        assert False, "should have raised"  # noqa: B011
    except TypeError:
        pass
    print("PASS test_append_non_serializable_raises")


def test_append_unicode():
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"msg": "こんにちは — café 🎉"})
    result = log.read_all()
    assert result == [{"msg": "こんにちは — café 🎉"}]
    print("PASS test_append_unicode")


def test_directory_auto_creation():
    base = tempfile.mkdtemp(prefix="tortoise_")
    p = os.path.join(base, "deeply", "nested", "dirs", "events.jsonl")
    log = EventLog(p)
    assert not os.path.exists(os.path.dirname(p))
    log.append({"created": True})
    assert os.path.exists(p)
    result = log.read_all()
    assert result == [{"created": True}]
    print("PASS test_directory_auto_creation")


# ── read_after / cursor tests (#688) ────────────────────────────────


def test_read_after_none_on_empty():
    log = EventLog(_tmp("events.jsonl"))
    assert log.read_after() == []
    assert log.read_after(None) == []
    print("PASS test_read_after_none_on_empty")


def test_read_after_none_returns_all():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    log.append({"n": 3})
    assert log.read_after() == [{"n": 1}, {"n": 2}, {"n": 3}]
    assert log.read_after(None) == [{"n": 1}, {"n": 2}, {"n": 3}]
    print("PASS test_read_after_none_returns_all")


def test_cursor_at_end_empty_log():
    log = EventLog(_tmp("events.jsonl"))
    token = log.cursor_at_end()
    assert isinstance(token, str) and len(token) > 0
    # Reading after empty-log cursor returns nothing (no events yet)
    assert log.read_after(token) == []
    print("PASS test_cursor_at_end_empty_log")


def test_cursor_at_end_no_new_events():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    # No new events appended after cursor snapshot
    assert log.read_after(token) == []
    print("PASS test_cursor_at_end_no_new_events")


def test_read_after_returns_only_new():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    token = log.cursor_at_end()  # snapshot after 2 events
    log.append({"n": 3})
    log.append({"n": 4})
    assert log.read_after(token) == [{"n": 3}, {"n": 4}]
    print("PASS test_read_after_returns_only_new")


def test_read_after_returns_empty_when_no_new():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    # Multiple polls with no appends should all return empty
    assert log.read_after(token) == []
    assert log.read_after(token) == []
    print("PASS test_read_after_returns_empty_when_no_new")


def test_invalid_cursor_raises():
    log = EventLog(_tmp("events.jsonl"))
    for bad in ("not-base64!", "", "   ", "eyJ2IjoyfQ=="):  # v=2 unsupported
        try:
            log.read_after(bad)
            assert False, f"should have raised for {bad!r}"  # noqa: B011
        except ValueError:
            pass
    print("PASS test_invalid_cursor_raises")


def test_cursor_beyond_end_returns_empty():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    # Encode a cursor pointing to index 99 (way beyond the single event)
    token = log._encode_cursor(99)
    assert log.read_after(token) == []
    print("PASS test_cursor_beyond_end_returns_empty")


def test_polling_e2e_pattern():
    """Simulate the polling pattern: append, snapshot, append, poll, repeat."""
    log = EventLog(_tmp("events.jsonl"))

    # Initial sync
    log.append({"type": "A", "n": 0})
    events = log.read_after()  # None → all
    assert len(events) == 1
    cursor = log.cursor_at_end()
    assert log.read_after(cursor) == []  # no new yet

    # First poll cycle
    log.append({"type": "B", "n": 1})
    log.append({"type": "C", "n": 2})
    new_events = log.read_after(cursor)
    assert new_events == [{"type": "B", "n": 1}, {"type": "C", "n": 2}]
    cursor = log.cursor_at_end()

    # Second poll cycle — no new events
    assert log.read_after(cursor) == []

    # Third poll cycle — one more
    log.append({"type": "D", "n": 3})
    assert log.read_after(cursor) == [{"type": "D", "n": 3}]
    print("PASS test_polling_e2e_pattern")


def test_cursor_is_stable():
    """A cursor obtained earlier still works after more appends."""
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token_after_1 = log.cursor_at_end()
    log.append({"n": 2})
    log.append({"n": 3})
    # Using the old cursor still returns exactly events 2,3
    assert log.read_after(token_after_1) == [{"n": 2}, {"n": 3}]
    print("PASS test_cursor_is_stable")


def test_read_after_with_unicode_events():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"msg": "こんにちは"})
    token = log.cursor_at_end()
    log.append({"msg": "— café 🎉"})
    assert log.read_after(token) == [{"msg": "— café 🎉"}]
    print("PASS test_read_after_with_unicode_events")


def test_read_after_single_event_batch():
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    token = log.cursor_at_end()
    log.append({"n": 2})
    assert log.read_after(token) == [{"n": 2}]
    print("PASS test_read_after_single_event_batch")


def test_read_after_idempotent_cursor():
    """Same cursor returns same (deterministic) results across calls."""
    log = EventLog(_tmp("events.jsonl"))
    log.append({"n": 1})
    log.append({"n": 2})
    token = log.cursor_at_end()
    log.append({"n": 3})
    result1 = log.read_after(token)
    result2 = log.read_after(token)
    assert result1 == result2 == [{"n": 3}]
    print("PASS test_read_after_idempotent_cursor")


def test_cursor_token_opaque():
    """Cursor tokens should be stable across encode/decode round-trips."""
    for idx in (-1, 0, 1, 42, 999):
        token = EventLog._encode_cursor(idx)
        assert isinstance(token, str)
        # Must not contain the raw index visibly
        assert str(idx) not in token
        assert EventLog._decode_cursor(token) == idx
    print("PASS test_cursor_token_opaque")


def test_append_read_all_roundtrips_line_separator_unicode():
    """A record whose content contains U+2028/U+2029/U+0085 round-trips.

    ``append`` writes with ``ensure_ascii=False``, so those separators land
    RAW inside the JSON string. ``read_all`` must split on ``"\\n"`` only —
    ``str.splitlines()`` would split this one record into two, and the first
    fragment would read as mid-file corruption (which the #3316 refusal then
    turns into a failed recovery).
    """
    p = _tmp("events.jsonl")
    log = EventLog(p)
    event = {
        "type": "PointAdded",
        "point": {"id": "sep-1", "content": "a\u2028b\u2029c\u0085d",
                  "context": "x"},
    }
    log.append(event)
    assert log.read_all() == [event]
    assert log.torn_trailing_count == 0
    print("PASS test_append_read_all_roundtrips_line_separator_unicode")


def test_torn_record_may_revive_state_is_conservative_over_all_types():
    """Classification uses EVERY legible ``"type"``, not the first one.

    The envelope carries ``type`` before any payload, but ``append`` is public
    and ``read_all`` parses arbitrary bytes: a nested payload dict can carry a
    ``type`` too. A first-match classifier would let a nested
    ``"type": "PointAdded"`` mask the envelope's ``EntityMutated`` and replay a
    hard delete away.
    """
    # A nested HARMLESS type before the envelope's REMOVAL type → refuse.
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded"}, "type": "EntityMutated", '
        '"op": "del')
    # The mirror (a nested removal type) also refuses — over-refusal is safe.
    assert torn_record_may_revive_state(
        '{"point": {"type": "EntityMutated"}, "type": "PointAdded", "po')
    # A wholly-harmless record keeps the tolerance.
    assert not torn_record_may_revive_state(
        '{"type": "PointAdded", "point": {"id": "t')
    # An unreadable type can never be proven harmless.
    assert torn_record_may_revive_state(
        '{"event_id": "01JTORN", "ts": "2026-09-11T00:00:0')
    print("PASS test_torn_record_may_revive_state_is_conservative_over_all_types")


def test_a_torn_event_recorded_is_refused_however_it_was_cut():
    """``EventRecorded`` is NOT allowlisted, because a torn prefix cannot
    prove the record lacked connector metadata.

    ``_upsert_event`` → ``_materialize_connector_source`` DELETEs the
    superseded ``(Source)-[:references]->(Event)`` edge and the orphaned
    ``:Source`` whenever the record carries a registered connector
    ``sourceKind`` or an explicit ``sourceUrl`` — and the connector emitters
    write those keys LAST (after ``type``/``eventId``/``eventKind``/…), so a
    tear almost anywhere lands BEFORE them. A prefix that merely happens not
    to show the key is not evidence of absence, so the type is refused rather
    than rescued by a marker-substring exception that would fail open.
    """
    # The S15/T12 shape (a session event, no source field) — refused anyway.
    assert torn_record_may_revive_state(
        '{"type": "EventRecorded", "id": "e1", "eventId": "s1"}')
    # A tear inside the keys that gate the destructive leg.
    assert torn_record_may_revive_state(
        '{"type": "EventRecorded", "id": "e1", "sourceUrl": "https://x')
    assert torn_record_may_revive_state(
        '{"type": "EventRecorded", "id": "e1", "sourceKind": "github')
    # A tear in the byte immediately BEFORE the marker: the substring test the
    # previous revision used returned False here, which is the fail-open hole.
    assert torn_record_may_revive_state(
        '{"type": "EventRecorded", "id": "e1", "sour')
    assert torn_record_may_revive_state(
        '{"type": "EventRecorded", "id": "e1", "source')
    # A tear far past the retained-prefix cap: the cap cannot be allowed to
    # hide the type and turn the record into a tolerated one.
    long_torn = ('{"type": "EventRecorded", "id": "e1", "body": "'
                 + "x" * 9000 + '", "sourceUrl": "https://x')
    assert torn_record_may_revive_state(long_torn)
    print("PASS test_a_torn_event_recorded_is_refused_however_it_was_cut")


def test_a_torn_session_recorded_is_refused():
    """``SessionRecorded`` is NOT allowlisted: its fold OVERWRITES
    ``capture_ok`` / ``capture_extractor`` / ``capture_redactions`` straight
    from the payload (projection/entities.py:1261-1266) and the SDK emits it as
    a capture's TRAILING record with ``capture_ok=False`` on a failed or keyless
    capture (sdk.py:4844).

    Dropping that tear restores ``capture_ok=NULL``, which ``hosted_api`` reads
    as the legacy "presumed captured" replay case (hosted_api.py:10041-10065),
    so the failed session silently stops being re-attempted while recovery
    reports success — a payload-only lifecycle property, exactly the
    ``EventRecorded`` shape, with a payload that is the COMMON case.
    """
    assert torn_record_may_revive_state(
        '{"type": "SessionRecorded", "id": "sess_1", "capture_ok": false')
    # Even with no lifecycle property legible, the type alone is refused.
    assert torn_record_may_revive_state(
        '{"type": "SessionRecorded", "id": "sess_1"}')
    print("PASS test_a_torn_session_recorded_is_refused")


def test_torn_record_with_a_truncated_envelope_type_is_refused():
    """A ``"type"`` KEY whose VALUE did not survive must not be masked.

    A payload-first record (``append`` is public and a journal is
    operator-editable) can carry a nested complete ``"type"`` BEFORE the
    envelope's, and a tear inside the envelope value leaves the key with no
    complete match — so a plain ``findall`` classifier sees only the innocuous
    nested type and TOLERATES a record whose real type is a removal.
    """
    # nested allowlisted type + envelope value cut mid-identifier
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, '
        '"type": "EntityMuta')
    # the same shape with the envelope value cut entirely away
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, "type": "')
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, "type":')
    # a KEY torn mid-token, in key position (fragment at EOF)
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, "ty')
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, "type')
    assert torn_record_may_revive_state(
        '{"point": {"type": "PointAdded", "id": "x"}, "t')
    # An ESCAPED occurrence inside a string value is not a key and must not
    # make a benign record unreadable.
    assert not torn_record_may_revive_state(
        '{"type": "PointAdded", "point": {"id": "x", '
        '"content": "said \\"type\\": \\"EntityMu')
    # A torn STRING VALUE that merely starts with `t` is not a torn type KEY
    # (it is not in key position) — the no-over-correction direction.
    assert not torn_record_may_revive_state(
        '{"type": "PointAdded", "point": {"id": "torn-1", "content": "t')
    assert not torn_record_may_revive_state(
        '{"type": "PointAdded", "point": {"id": "torn-1", "content": "typ')
    print("PASS test_torn_record_with_a_truncated_envelope_type_is_refused")


def test_describe_torn_tail_revival_names_every_legible_type():
    """The operator message must name EVERY legible type, not the first.

    Classification refuses when ANY legible type is outside the allowlist, so a
    message built from ``types[0]`` could print an allowlisted type beside the
    refusal it is explaining. An unreadable type is named too.
    """
    msg = describe_torn_tail_revival(
        ['{"type": "EntityMutated", "point": {"type": "PointAdded"}, '
         '"op": "del'])
    assert "EntityMutated" in msg, msg
    assert "PointAdded" in msg, msg
    # A truncated type VALUE is reported as unreadable, alongside what survived.
    msg2 = describe_torn_tail_revival(
        ['{"point": {"type": "PointAdded"}, "type": "EntityMuta'])
    assert "<unreadable>" in msg2, msg2
    assert "PointAdded" in msg2, msg2
    # Nothing legible at all.
    assert describe_torn_tail_revival(["{\"event_id\": \"01J"]) == "<unreadable>"
    print("PASS test_describe_torn_tail_revival_names_every_legible_type")


def test_torn_born_terminal_point_added_is_a_disclosed_tolerance():
    """DISCLOSED EXCEPTION to the allowlist criterion — see tortoise/log.py.

    ``PointAdded``/``OperatorAdded`` folds write ``n.status`` straight from the
    payload (``entities.py:770``/``:795``) and ``create_point`` accepts terminal
    statuses (``sdk.py:3489``), so a BORN-TERMINAL create journals its terminal
    ``status`` in the ``PointAdded`` point snapshot (``sdk.py:3828``). A torn
    born-terminal ``PointAdded`` for an id an EARLIER record left live is
    therefore a resurrection this classifier TOLERATES.

    Pinned so the tolerance is visible and a future tightening is deliberate
    rather than silent. Filed as #5921. ``EventRecorded`` is NOT tolerated for
    the same reason: its connector leg is reached by EVERY connector ingest, so
    there the harmful payload is the common case, not a narrow re-create.
    """
    assert not torn_record_may_revive_state(
        '{"type": "PointAdded", "point": {"id": "x", "status": "superseded"')
    assert not torn_record_may_revive_state(
        '{"type": "OperatorAdded", "point": {"id": "x", "status": "retracted"')
    print("PASS test_torn_born_terminal_point_added_is_a_disclosed_tolerance")


def test_torn_tail_allowlist_holds_no_destructive_type():
    """The allowlist IS the fail-open/fail-closed switch, so pin its POLARITY
    and its CONTENT.

    Behavioural tests cover a handful of members; without an exact pin an edit
    that typos a member name (silently dropping the tolerance for the intended
    type) or drops an audit member would pass unnoticed. The full-equality
    assertion makes ANY edit to the set a deliberate, visible test change.

    The `destructive` assertion below is NOT redundant with that pin: it is the
    only check that catches a terminal type moved into BOTH the set and the
    equality literal in the same commit. It cannot catch a type NEITHER list
    knows about — a brand-new terminal type added to both would pass here — so
    it pins the KNOWN terminal vocabulary, not the class.
    """
    # Exact membership: any addition, removal or typo must update this literal.
    assert frozenset({
        # point / operator lifecycle additions and updates (MERGE + SET)
        "PointAdded", "OperatorAdded", "PointRevised", "OperatorAnnotated",
        "PointPromoted", "OperatorPromoted",
        # object / subject lane additions (+ a derived-embedding clear)
        "ObjectRegistered", "SubjectAdded",
        # source lane addition
        "SourceCreated",
        # additive edge write
        "EntityLinked",
        # `_NO_PROJECTION_FOLD` audit records: NO fold in any dispatcher
        "BatchIdStamped", "IngestStarted", "CalibrationRecorded",
        "DedupeRecorded", "DedupeRejected", "EntityBindingRefused",
        "DirectEdgeCreated",
    }) == TORN_TAIL_HARMLESS_EVENT_TYPES
    # Named terminal / removal types that would resurrect state if tolerated.
    # PointInvalidated / PointSuperseded are EP-terminal (a dropped one lets a
    # withdrawn claim vote); EntityMutated op=delete and PointsMerged hard-delete.
    destructive = {
        "PointRetracted", "PointSuperseded", "PointInvalidated",
        "PointsMerged", "EntityMutated", "ObjectSuperseded",
        "ConfidenceChanged", "DocumentCreated", "DirectEdgeRepoint",
        "EventRecorded",
    }
    offenders = sorted(destructive & TORN_TAIL_HARMLESS_EVENT_TYPES)
    assert not offenders, f"destructive types in the allowlist: {offenders}"
    assert all(isinstance(t, str) and t for t in TORN_TAIL_HARMLESS_EVENT_TYPES)
    print("PASS test_torn_tail_allowlist_holds_no_destructive_type")


def test_a_destructive_type_anywhere_in_a_torn_tail_is_refused():
    """Classification reads the WHOLE torn line, uncapped.

    ``read_all`` retains the full line, because a cap would let a
    non-allowlisted type hiding beyond it ride an allowlisted envelope type
    before it — fail open. This pins both directions: a destructive type deep
    in a large record is found, and an allowlisted envelope type alone is not
    allowed to mask it.
    """
    p = _tmp("events.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "PointAdded",
                             "point": {"id": "a"}}) + "\n")
        # An allowlisted envelope type, ~5000 filler bytes, then a REMOVAL type
        # that a 4096-byte cap would have chopped off.
        fh.write('{"type": "PointAdded", "point": {"id": "b", "pad": "'
                 + "x" * 5000 + '", "type": "EntityMutated", "op": "del')
    log = EventLog(p)
    assert len(log.read_all()) == 1
    assert log.torn_trailing_count == 1
    assert len(log.torn_trailing_raw[0]) > 4096, "the torn line must be retained whole"
    assert len(log.torn_tail_revival_records()) == 1

    # A huge torn record whose type did not survive at all is also refused.
    p2 = _tmp("events.jsonl")
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write('{"pad": "' + "x" * 9000)  # malformed, no type
    log2 = EventLog(p2)
    assert log2.read_all() == []
    assert len(log2.torn_tail_revival_records()) == 1
    print("PASS test_a_destructive_type_anywhere_in_a_torn_tail_is_refused")


def test_read_all_resets_torn_state_across_calls():
    """A reused ``EventLog`` must never report a previous call's tear.

    ``torn_trailing_raw`` is the state a replay engine consults before
    refusing, so a stale list is a rewrite of another journal's verdict.
    """
    p = _tmp("events.jsonl")
    log = EventLog(p)
    log.append({"type": "PointAdded", "point": {"id": "a"}})
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"type": "EntityMutated", "op": "del')  # torn, unterminated
    log.read_all()
    assert log.torn_trailing_count == 1
    assert len(log.torn_trailing_raw) == 1
    assert len(log.torn_tail_revival_records()) == 1

    clean = _tmp("events.jsonl")
    EventLog(clean).append({"type": "PointAdded", "point": {"id": "b"}})
    log.path = Path(clean)
    assert log.read_all() == [{"type": "PointAdded", "point": {"id": "b"}}]
    assert log.torn_trailing_count == 0
    assert log.torn_trailing_raw == []
    assert log.torn_tail_revival_records() == []

    # The reset must also hold on the MISSING-FILE early return: a stale tear
    # list there would make a replay engine refuse a journal it never read.
    with open(p, "a", encoding="utf-8") as fh:
        fh.write('{"type": "EntityMutated", "op": "del')  # torn again
    log.path = Path(p)
    log.read_all()
    assert log.torn_trailing_count == 1
    log.path = Path(_tmp("does-not-exist.jsonl"))
    assert log.read_all() == []
    assert log.torn_trailing_count == 0
    assert log.torn_trailing_raw == []
    assert log.torn_tail_revival_records() == []
    print("PASS test_read_all_resets_torn_state_across_calls")


def test_read_all_tolerates_a_malformed_newline_terminated_final_line():
    """The S15 tolerance covers a malformed LAST line whether or not its
    terminator survived the crash. ``read_all`` drops exactly one terminator
    before splitting, so both byte shapes take the torn-tail branch — while
    the split still honours ``"\n"`` only (U+2028 in content is not a line)."""
    p = _tmp("events.jsonl")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "PointAdded",
                             "point": {"id": "a"}}) + "\n")
        fh.write("{not json" + "\n")  # malformed but TERMINATED
    log = EventLog(p)
    events = log.read_all()
    assert len(events) == 1 and events[0]["point"]["id"] == "a"
    assert log.torn_trailing_count == 1
    print("PASS test_read_all_tolerates_a_malformed_newline_terminated_final_line")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall EventLog tests passed")


if __name__ == "__main__":
    _run_all()
