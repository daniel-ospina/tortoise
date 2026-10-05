"""#5917: a torn tail is sealed by the next append, and read byte-safely.

Two defects, both measured on unmodified `tortoise/log.py`, both in the
torn-tail contract that exists so a crash mid-append is survivable:

1. **A torn tail + the next append lost a record.** `append` wrote onto the
   unterminated fragment (`{"partial` + `{"next": 1}`), the merged line was
   malformed and FINAL, so `read_all` skipped it as a torn tail and the record
   `append` had written was gone — reported only as a tear.
2. **A tear inside a multi-byte character raised `UnicodeDecodeError`.** The
   read decoded the whole file strictly (`read_text`), so the tear never
   reached the #3316 classifier and the journal stayed unreadable — the
   recovery tool the journal exists for was dead, permanently (#5917).

The fix seals a torn tail instead of appending onto it: the fragment is
terminated and annotated by a marker line, so the next record starts on a line
of its own, the fragment is still classified, and NOTHING is rewritten —
sealing is another append, so backup/restore and the append-only contract are
untouched.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.log import (  # noqa: E402, RUF100
    SEALED_TORN_TYPE,
    EventLog,
    TornTailResurrectionError,
    refuse_torn_tail_revival,
)


def _tmp(name: str = "events.jsonl") -> str:
    return os.path.join(tempfile.mkdtemp(prefix="tortoise_"), name)


def _raw(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def _write_torn_tail(path: str | Path, fragment: bytes) -> None:
    """Put *fragment* on disk as an unterminated trailing record (SIGKILL shape)."""
    with Path(path).open("ab") as fh:
        fh.write(fragment)


# ── defect 2: the torn multi-byte tail (#5917's own repro) ──────────────


def test_a_torn_multibyte_tail_is_classified_not_raised():
    p = _tmp()
    with open(p, "wb") as fh:
        fh.write((json.dumps({"type": "PointAdded", "point": {"id": "a"}}) + "\n").encode())
        # cut INSIDE a 3-byte codepoint (U+2028 = e2 80 a8), 2 bytes kept
        fh.write(b'{"type": "PointAdded", "point": {"id": "b", "content": "\xe2\x80')
    log = EventLog(p)

    assert log.read_all() == [{"type": "PointAdded", "point": {"id": "a"}}]
    assert log.torn_trailing_count == 1
    assert len(log.torn_trailing_raw) == 1, "the fragment must be retained for #3316"
    print("PASS test_a_torn_multibyte_tail_is_classified_not_raised")


def test_an_undecodable_fragment_is_never_dropped_without_counting():
    """The fail-OPEN trap: an undecodable line must not look like an empty one.

    Mapping a decode failure to "" and continuing past it would drop the
    fragment WITHOUT counting or classifying it, so a replay that must refuse
    would silently proceed.
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"type": "EventRecorded", "id": "e1"})
    _write_torn_tail(p, b'{"type": "EventRecorded", "id": "e2", "body": "\xe2\x98')
    reader = EventLog(p)

    assert reader.read_all() == [{"type": "EventRecorded", "id": "e1"}]
    assert reader.torn_trailing_count == 1
    assert reader.torn_tail_revival_records(), (
        "an unreadable type cannot be proven harmless — it must be refused")
    print("PASS test_an_undecodable_fragment_is_never_dropped_without_counting")


# ── defect 1: the record appended after a tear must survive ─────────────


def test_an_append_after_a_tear_keeps_the_new_record():
    p = _tmp()
    log = EventLog(p)
    for i in range(3):
        log.append({"id": f"good-{i}"})
    _write_torn_tail(p, b'{"id": "torn-3", "blob": "AAAA')

    # The tear is tolerated on its own, as before.
    assert EventLog(p).read_all() == [{"id": f"good-{i}"} for i in range(3)]

    log.append({"id": "next-4"})          # <-- this record used to be lost
    records = EventLog(p).read_all()
    assert records == [{"id": f"good-{i}"} for i in range(3)] + [{"id": "next-4"}], records
    print("PASS test_an_append_after_a_tear_keeps_the_new_record")


def test_many_appends_after_a_tear_all_survive():
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"id": "torn-1", "text": "\xe2\x98')  # torn mid-codepoint too
    for i in range(2, 6):
        log.append({"id": f"next-{i}"})
    records = EventLog(p).read_all()
    assert [r["id"] for r in records] == ["good-0", "next-2", "next-3", "next-4", "next-5"], records
    print("PASS test_many_appends_after_a_tear_all_survive")


# ── the #3316 refusal must still fire, before and after sealing ─────────


def test_the_sealed_fragment_still_reaches_the_3316_classifier():
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    # `EventRecorded` is deliberately NOT in TORN_TAIL_HARMLESS_EVENT_TYPES.
    _write_torn_tail(p, b'{"type": "EventRecorded", "id": "e1", "body": "x')
    before = EventLog(p)
    before.read_all()
    assert before.torn_tail_revival_records() == [before.torn_trailing_raw[0]]

    log.append({"id": "next-1"})   # sealing moves the fragment off the tail
    after = EventLog(p)
    records = after.read_all()
    assert {"id": "next-1"} in records, "the new record must not be lost"
    revive = after.torn_tail_revival_records()
    assert revive, (
        "sealing must not hide the fragment from the #3316 classifier — that "
        "would be the fail-open direction the refusal exists to prevent")
    try:
        refuse_torn_tail_revival(revive)
    except TornTailResurrectionError:
        pass
    else:
        raise AssertionError("a sealed EventRecorded tear must still refuse a replay")
    print("PASS test_the_sealed_fragment_still_reaches_the_3316_classifier")


def test_a_refused_replay_still_refuses_after_a_seal():
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"type": "PointRevised", "id": "p1"')
    log.append({"id": "next-1"})
    reader = EventLog(p)
    reader.read_all()
    try:
        refuse_torn_tail_revival(reader.torn_tail_revival_records())
    except TornTailResurrectionError:
        pass
    else:
        raise AssertionError("a removal tear sealed into the journal must still refuse")
    print("PASS test_a_refused_replay_still_refuses_after_a_seal")


def test_a_harmless_sealed_fragment_is_not_reported_as_revival():
    """The refusal stays proportional: only an unprovable loss refuses."""
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "a", "content": "x')
    log.append({"id": "next-1"})
    reader = EventLog(p)
    reader.read_all()
    assert reader.torn_trailing_count == 1
    assert reader.torn_tail_revival_records() == []
    print("PASS test_a_harmless_sealed_fragment_is_not_reported_as_revival")


# ── append-only, idempotent, and the fail-closed guards stay ────────────


def test_sealing_only_appends_never_rewrites():
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    before = _raw(p)
    _write_torn_tail(p, b'{"id": "torn-1"')
    with_tear = _raw(p)
    log.append({"id": "next-2"})
    after = _raw(p)

    assert after.startswith(with_tear), "sealing truncated or rewrote existing bytes"
    assert with_tear.startswith(before)
    assert json.dumps({"type": SEALED_TORN_TYPE}) in after.decode()
    print("PASS test_sealing_only_appends_never_rewrites")


def test_the_marker_line_is_never_returned_as_a_record():
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"id": "torn-0"')
    log.append({"id": "next-1"})
    log.append({"id": "next-2"})
    records = EventLog(p).read_all()
    assert records == [{"id": "next-1"}, {"id": "next-2"}]
    assert all(r.get("type") != SEALED_TORN_TYPE for r in records)
    print("PASS test_the_marker_line_is_never_returned_as_a_record")


def test_the_count_is_stable_across_reads():
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"id": "torn-0"')
    log.append({"id": "next-1"})
    counts = []
    for _ in range(3):
        reader = EventLog(p)
        reader.read_all()
        counts.append((reader.torn_trailing_count, len(reader.torn_trailing_raw)))
    assert counts == [(1, 1)] * 3, counts
    print("PASS test_the_count_is_stable_across_reads")


def test_a_clean_journal_gains_no_marker():
    p = _tmp()
    log = EventLog(p)
    for i in range(3):
        log.append({"id": f"good-{i}"})
    assert SEALED_TORN_TYPE not in _raw(p).decode()
    reader = EventLog(p)
    assert reader.read_all() == [{"id": f"good-{i}"} for i in range(3)]
    assert reader.torn_trailing_count == 0 and reader.torn_trailing_raw == []
    print("PASS test_a_clean_journal_gains_no_marker")


def test_a_missing_or_empty_journal_is_not_torn():
    missing = _tmp()
    log = EventLog(missing)
    assert log.read_all() == []
    assert log.torn_trailing_count == 0
    log.append({"id": "a"})            # first write into a missing file
    assert SEALED_TORN_TYPE not in _raw(missing).decode()

    empty = _tmp()
    Path(empty).write_bytes(b"")
    empty_log = EventLog(empty)
    assert empty_log.read_all() == []
    empty_log.append({"id": "b"})
    assert SEALED_TORN_TYPE not in _raw(empty).decode()
    print("PASS test_a_missing_or_empty_journal_is_not_torn")


def test_a_whole_unterminated_file_is_sealed():
    """No newline anywhere: the entire file is one fragment."""
    p = _tmp()
    Path(p).write_bytes(b'{"id": "torn-0", "blob": "AAA')
    log = EventLog(p)
    log.append({"id": "next-1"})
    records = EventLog(p).read_all()
    assert records == [{"id": "next-1"}], records
    print("PASS test_a_whole_unterminated_file_is_sealed")


def test_a_mid_file_malformed_line_still_raises():
    """No marker ⇒ the #3316 posture is unchanged: refuse, never skip."""
    p = _tmp()
    good = json.dumps({"id": "good-0"}) + "\n"
    Path(p).write_text(good + "{not json}\n" + json.dumps({"id": "good-2"}) + "\n",
                       encoding="utf-8")
    reader = EventLog(p)
    try:
        reader.read_all()
    except ValueError as exc:
        assert "mid-file corruption" in str(exc)
    else:
        raise AssertionError("an unmarked mid-file malformed line must still raise")
    print("PASS test_a_mid_file_malformed_line_still_raises")


def test_a_mid_file_undecodable_line_still_raises():
    p = _tmp()
    Path(p).write_bytes(
        json.dumps({"id": "good-0"}).encode() + b"\n"
        + b'{"id": "broken-\xe2\x98"}\n'
        + json.dumps({"id": "good-2"}).encode() + b"\n"
    )
    reader = EventLog(p)
    try:
        reader.read_all()
    except ValueError as exc:
        assert "mid-file corruption" in str(exc)
    else:
        raise AssertionError("an unmarked mid-file undecodable line must still raise")
    print("PASS test_a_mid_file_undecodable_line_still_raises")


def test_a_sealed_fragment_at_the_end_is_still_counted():
    """A crash after the seal but before the record: the fragment stays visible."""
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"type": "EventRecorded", "id": "e1"')
    log.append({"id": "next-1"})
    # drop the record the append wrote, leaving `...\nfragment\nmarker\n`
    data = _raw(p)
    cut = data.rindex(b'{"id": "next-1"}')
    Path(p).write_bytes(data[:cut])

    reader = EventLog(p)
    assert reader.read_all() == [{"id": "good-0"}]
    assert reader.torn_trailing_count == 1
    assert reader.torn_tail_revival_records(), "the sealed fragment is still classified"
    print("PASS test_a_sealed_fragment_at_the_end_is_still_counted")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL PASS")
