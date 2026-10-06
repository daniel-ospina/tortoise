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
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.log import (  # noqa: E402, RUF100
    SEAL_SENTINEL,
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
    assert SEAL_SENTINEL in after.decode()
    print("PASS test_sealing_only_appends_never_rewrites")


def test_the_marker_line_is_never_returned_as_a_record():
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"id": "torn-0"')
    log.append({"id": "next-1"})
    log.append({"id": "next-2"})
    records = EventLog(p).read_all()
    assert records == [{"id": "next-1"}, {"id": "next-2"}]
    assert all(r.get("type") != SEAL_SENTINEL for r in records)
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
    assert SEAL_SENTINEL not in _raw(p).decode()
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
    assert SEAL_SENTINEL not in _raw(missing).decode()

    empty = _tmp()
    Path(empty).write_bytes(b"")
    empty_log = EventLog(empty)
    assert empty_log.read_all() == []
    empty_log.append({"id": "b"})
    assert SEAL_SENTINEL not in _raw(empty).decode()
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


def test_a_seal_torn_at_every_prefix_stays_readable():
    """The seal itself is written by the same non-atomic write it defends.

    A tear inside the marker used to leave the fragment mid-file with an
    unrecognizable successor, so read_all raised and NO later append could
    repair it (the seal only adds a marker one line further down). Verified
    for every marker prefix, since the seal is `\n` + marker + `\n` + record.
    """
    marker = SEAL_SENTINEL.encode()
    for cut in range(0, len(marker) + 1):
        p = _tmp()
        log = EventLog(p)
        log.append({"id": "good-0"})
        _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "a"')
        # a crash landing inside the seal: the fragment's terminator and part
        # of the marker reached the disk
        _write_torn_tail(p, b"\n" + marker[:cut])
        log.append({"id": "next-1"})
        records = EventLog(p).read_all()
        assert [r["id"] for r in records] == ["good-0", "next-1"], (cut, records)
        assert len(records) == 2, (cut, records)
    print("PASS test_a_seal_torn_at_every_prefix_stays_readable")


def test_a_torn_seal_does_not_over_refuse():
    """The partial marker is an annotation, not a fragment: one tear, one count."""
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "a"')
    _write_torn_tail(p, b"\n" + SEAL_SENTINEL[:4].encode())
    log.append({"id": "next-1"})
    reader = EventLog(p)
    reader.read_all()
    assert reader.torn_trailing_count == 1, reader.torn_trailing_raw
    assert reader.torn_tail_revival_records() == [], (
        "a PointAdded fragment is harmless; the partial marker must not add a "
        "second, unclassifiable fragment")
    print("PASS test_a_torn_seal_does_not_over_refuse")


def test_a_bare_cr_still_separates_records():
    """`Path.read_text` translated universal newlines; the byte read must too."""
    p = _tmp()
    Path(p).write_bytes(b'{"id": "a"}\r{"id": "b"}\r')
    reader = EventLog(p)
    assert reader.read_all() == [{"id": "a"}, {"id": "b"}]
    assert reader.torn_trailing_count == 0

    crlf = _tmp()
    Path(crlf).write_bytes(b'{"id": "a"}\r\n{"id": "b"}\r\n')
    assert EventLog(crlf).read_all() == [{"id": "a"}, {"id": "b"}]
    print("PASS test_a_bare_cr_still_separates_records")


def test_the_sentinel_cannot_be_a_record_prefix():
    """The seal is non-JSON precisely so no record can look like one.

    With a JSON marker, `{` and `{"` are prefixes of BOTH the marker and every
    record, so a torn record fragment could be swallowed as an annotation and
    never classified (fail-open). A sentinel no record can begin with removes
    the ambiguity by construction, and a record that *mentions* the sentinel
    in a value is still an ordinary record.
    """
    import tortoise.log as logmod

    assert not SEAL_SENTINEL.startswith('{'), "the sentinel must not be JSON-shaped"
    for prefix_len in range(1, len(SEAL_SENTINEL)):
        assert logmod._is_seal_prefix(SEAL_SENTINEL[:prefix_len])

    p = _tmp()
    log = EventLog(p)
    log.append({"type": "PointAdded", "id": "kept", "note": SEAL_SENTINEL})
    assert [r["id"] for r in EventLog(p).read_all()] == ["kept"]
    print("PASS test_the_sentinel_cannot_be_a_record_prefix")


def test_the_cursor_follows_records_across_a_seal():
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "a"})
    log.append({"id": "b"})
    cursor = log.cursor_at_end()
    _write_torn_tail(p, b'{"id": "torn"')
    log.append({"id": "c"})
    assert log.read_after(cursor) == [{"id": "c"}]
    print("PASS test_the_cursor_follows_records_across_a_seal")


def test_a_torn_record_fragment_is_not_mistaken_for_a_marker():
    """Every record starts `{"`, so a prefix marker test would swallow these.

    They are torn RECORDS: unreadable-type tears the #3316 classifier refuses.
    Skipping them as "annotations" would be the fail-open direction.
    """
    for fragment in (b"{", b'{"', b'{"t', b'{"type": "', b'{"type": "PointRetracted"'):
        p = _tmp()
        log = EventLog(p)
        log.append({"id": "good-0"})
        _write_torn_tail(p, fragment)
        log.append({"id": "next-1"})       # would previously merge into it
        reader = EventLog(p)
        records = reader.read_all()
        assert {"id": "next-1"} in records, (fragment, records)
        assert reader.torn_trailing_count == 1, (fragment, reader.torn_trailing_raw)
        assert reader.torn_tail_revival_records(), (
            f"a torn record fragment {fragment!r} must be refused, not skipped")
    print("PASS test_a_torn_record_fragment_is_not_mistaken_for_a_marker")


def test_a_mid_file_marker_prefix_fragment_still_raises():
    """A `{"` fragment mid-file is corruption, not an annotation."""
    p = _tmp()
    Path(p).write_bytes(
        b'{"id": "good-0"}\n'
        b'{"type": "'                                  # a torn record, no marker
        b'\n{"id": "good-2"}\n')
    reader = EventLog(p)
    try:
        reader.read_all()
    except ValueError as exc:
        assert "mid-file corruption" in str(exc)
    else:
        raise AssertionError("a mid-file torn-record fragment must still raise")
    print("PASS test_a_mid_file_marker_prefix_fragment_still_raises")


def test_a_terminated_malformed_tail_is_marked_and_heals():
    """A seal torn at byte 0: the fragment is terminated but unmarked.

    A last-byte check reports "clean" forever, so no marker is ever written
    again and the next record makes the fragment mid-file corruption — a
    permanently unreadable journal (measured).
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "a"')
    _write_torn_tail(p, b"\n")           # only the seal's terminator landed
    for i in (1, 2, 3):
        log.append({"id": f"next-{i}"})
        reader = EventLog(p)
        records = reader.read_all()
        assert {"id": f"next-{i}"} in records, (i, records)
        assert reader.torn_trailing_count == 1, (i, reader.torn_trailing_raw)
    print("PASS test_a_terminated_malformed_tail_is_marked_and_heals")


def test_the_seal_does_not_add_a_blank_line_when_marking_a_terminated_tail():
    p = _tmp()
    log = EventLog(p)
    _write_torn_tail(p, b'{"type": "PointAdded"')
    _write_torn_tail(p, b"\n")
    log.append({"id": "next-1"})
    assert b"\n\n" not in _raw(p), _raw(p)
    print("PASS test_the_seal_does_not_add_a_blank_line_when_marking_a_terminated_tail")


def test_the_seal_is_correct_at_window_boundaries():
    """A last line at/around the scan window must not be misread as clean.

    Measured failure before this test: a last line of exactly the window size
    made the seal report "clean", so the next record merged into the fragment
    and was lost — the #5917 defect, from the seal's own scan.
    """
    window = 1 << 16
    for length in (window - 1, window, window + 1, 2 * window):
        for shape in ("unterminated", "terminated-malformed", "terminated-valid"):
            p = _tmp()
            log = EventLog(p)
            log.append({"id": "good-0"})
            if shape == "unterminated":
                body = b'{"id": "frag", "pad": "' + b"a" * length
            elif shape == "terminated-malformed":
                body = b'{"id": "frag", "pad": "' + b"a" * length + b"\n"
            else:
                body = (b'{"id": "valid", "pad": "' + b"a" * length + b'"}\n')
            _write_torn_tail(p, body)
            log.append({"id": "next-1"})
            log.append({"id": "next-2"})
            reader = EventLog(p)
            records = reader.read_all()
            ids = [r["id"] for r in records]
            assert "next-1" in ids and "next-2" in ids, (length, shape, ids)
            if shape != "terminated-valid":
                assert reader.torn_trailing_count == 1, (length, shape, reader.torn_trailing_raw[:1])
            assert b"\n\n" not in _raw(p), (length, shape, "blank line added")
    print("PASS test_the_seal_is_correct_at_window_boundaries")


def test_a_terminated_undecodable_tail_heals():
    """A terminated line that is not valid UTF-8 is MALFORMED, not empty."""
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"id": "torn-1", "text": "\xe2\x80')
    _write_torn_tail(p, b"\n")          # the seal's terminator landed, the marker did not
    for i in (1, 2, 3):
        log.append({"id": f"next-{i}"})
        reader = EventLog(p)
        ids = [r["id"] for r in reader.read_all()]
        assert f"next-{i}" in ids, (i, ids)
        assert reader.torn_trailing_count == 1, (i, reader.torn_trailing_raw)
    print("PASS test_a_terminated_undecodable_tail_heals")


def test_a_terminated_undecodable_only_file_heals():
    p = _tmp()
    Path(p).write_bytes(b"\xe2\x80\n")
    log = EventLog(p)
    log.append({"id": "next-1"})
    reader = EventLog(p)
    assert [r["id"] for r in reader.read_all()] == ["next-1"]
    assert reader.torn_trailing_count == 1
    print("PASS test_a_terminated_undecodable_only_file_heals")


def test_a_terminated_malformed_tail_is_adopted_and_classified():
    """CONTRACT: a terminated-malformed LAST line is a torn fragment.

    The seal can land its terminator without its marker (a crash at byte 0 of
    the seal), which is byte-identical to an externally written partial record
    that someone terminated. Both are adopted by the next `append`, and the
    decision about whether dropping them is safe stays where #3316 puts it: a
    fragment that cannot be proven harmless REFUSES the replay. Before this
    change such a line raised ValueError out of read_all at PARSE time, so the
    refusal moves one layer later (TornTailResurrectionError, a RuntimeError)
    for this shape only.
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b"{not valid json")
    _write_torn_tail(p, b"\n")
    log.append({"id": "next-1"})

    reader = EventLog(p)
    assert [r["id"] for r in reader.read_all()] == ["good-0", "next-1"]
    assert reader.torn_trailing_count == 1
    assert reader.torn_tail_revival_records() == ["{not valid json"]
    try:
        refuse_torn_tail_revival(reader.torn_tail_revival_records())
    except TornTailResurrectionError:
        pass
    else:
        raise AssertionError("an unclassifiable fragment must refuse the replay")
    print("PASS test_a_terminated_malformed_tail_is_adopted_and_classified")


def test_an_adopted_harmless_fragment_is_counted_not_refused():
    """The allowed direction of that contract, pinned so it is not incidental.

    A terminated prefix of an allowlisted record is a tear by classification:
    dropping it is the data-LOSS direction the tolerance exists for, and the
    count is the signal. This is the only shape whose parse-time ValueError
    moved (a mid-file line at EOF is still a tear either way).
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "lost"')
    _write_torn_tail(p, b"\n")
    log.append({"id": "next-1"})

    reader = EventLog(p)
    assert [r["id"] for r in reader.read_all()] == ["good-0", "next-1"]
    assert reader.torn_trailing_count == 1, "the dropped fragment is reported"
    assert reader.torn_tail_revival_records() == []
    print("PASS test_an_adopted_harmless_fragment_is_counted_not_refused")


def test_the_backup_count_survives_a_multibyte_tear():
    """`restore` must not die on the tear read_all now survives."""
    from tortoise.backup import restore

    root = Path(tempfile.mkdtemp())
    backup_dir = root / "backup"
    backup_dir.mkdir()
    (backup_dir / "events.jsonl").write_bytes(
        json.dumps({"type": "PointAdded", "point": {"id": "a"}}).encode() + b"\n"
        + b'{"type": "PointAdded", "point": {"id": "b", "content": "\xe2\x80')
    work = root / "work"
    work.mkdir()
    result = restore(str(backup_dir), str(work / "r.db"),
                     events_path=str(work / "events.jsonl"))
    assert result["status"] == "ok", result
    assert result["events"] == 2, result   # one record + one torn fragment
    print("PASS test_the_backup_count_survives_a_multibyte_tear")


def test_the_backup_count_agrees_with_the_reader():
    """`restore`'s event count must equal what the reader sees — records plus
    tolerated torn fragments — and must not count a seal annotation, complete
    or torn, or die on a multi-byte or CR-separated journal."""
    from tortoise.backup import restore

    marker = SEAL_SENTINEL.encode()
    cases = {
        # 1 record + 1 torn fragment -> 2
        "torn": b'{"type": "PointAdded", "point": {"id": "a"}}\n{"id": "torn"',
        # records + a crashed seal (fragment, torn marker, marker) -> 3
        "torn-seal": (b'{"id": "a"}\n{"id": "torn"}\n{"ty\n'
                      + marker + b'\n{"id": "b"}\n'),
        # a multi-byte tear must not raise
        "multibyte": (b'{"type": "PointAdded", "point": {"id": "a"}}\n'
                      b'{"id": "b", "text": "\xe2\x80'),
        # bare-CR separated records -> 3
        "bare-cr": b'{"id": "a"}\r{"id": "b"}\r{"id": "c"}\r',
    }
    for name, payload in cases.items():
        root = Path(tempfile.mkdtemp())
        backup_dir = root / "backup"
        backup_dir.mkdir()
        (backup_dir / "events.jsonl").write_bytes(payload)
        work = root / "work"
        work.mkdir()

        reader = EventLog(backup_dir / "events.jsonl")
        records = reader.read_all()
        expected = len(records) + reader.torn_trailing_count

        result = restore(str(backup_dir), str(work / "r.db"),
                         events_path=str(work / "events.jsonl"))
        assert result["status"] == "ok", (name, result)
        assert result["events"] == expected, (name, result, expected, records)
    print("PASS test_the_backup_count_agrees_with_the_reader")


def test_a_complete_record_that_lost_only_its_newline_is_terminated():
    """The `unterminated + parses` arm: terminate it, do NOT annotate it.

    A crash that lands after a record's last byte but before its `\n` is
    ordinary, and the annotation is not just redundant: a tear of it would
    leave a sentinel prefix after a VALID record, which refuses every later
    replay. Round 11 measured that mutating this arm to write the sentinel
    still passed all 40 tests and made this shape permanently unreadable.
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "r0"})
    with open(p, "ab") as f:
        f.write(b'{"id": "r1"}')          # the terminating newline was lost
    log.append({"id": "r2"})

    body = _raw(p)
    assert SEAL_SENTINEL.encode() not in body, body
    assert body == b'{"id": "r0"}\n{"id": "r1"}\n{"id": "r2"}\n', body
    reader = EventLog(p)
    assert [r["id"] for r in reader.read_all()] == ["r0", "r1", "r2"]
    assert reader.torn_trailing_count == 0, reader.torn_trailing_raw
    print("PASS test_a_complete_record_that_lost_only_its_newline_is_terminated")


def test_the_backup_count_never_parses_the_journal(monkeypatch):
    """The reported count must not json-parse every record (290x measured).

    Parsing to produce a number this surface only REPORTS cost 20.4 s on a
    300,000-record journal against 0.07 s unparsed — and on the
    `into_falkor=True` path the journal was already parsed once. Pinning it by
    making the parse impossible: if the count path parses, this raises.
    """
    import json as json_mod

    from tortoise.backup import restore

    root = Path(tempfile.mkdtemp())
    backup_dir = root / "backup"
    backup_dir.mkdir()
    payload = b"".join(b'{"id": "r%d"}\n' % i for i in range(500))
    (backup_dir / "events.jsonl").write_bytes(payload)
    work = root / "work"
    work.mkdir()

    def _boom(*a, **k):
        raise AssertionError("the event count parsed the journal")

    monkeypatch.setattr(json_mod, "loads", _boom)
    result = restore(str(backup_dir), str(work / "r.db"),
                     events_path=str(work / "events.jsonl"))
    assert result["status"] == "ok", result
    assert result["events"] == 500, result
    print("PASS test_the_backup_count_never_parses_the_journal")


def test_the_backup_count_agrees_with_the_reader_on_hard_shapes():
    """Sentinel-prefix lines, adjacent torn seals, and a 4 MiB record.

    The count is now computed from LINE SHAPE rather than by reading, so it
    must still agree with `len(read_all()) + torn_trailing_count` — including
    a TORN annotation (`__Torn`), which is not an event, and an adjacent torn
    seal that is not its fragment's successor.
    """
    from tortoise.backup import restore

    marker = SEAL_SENTINEL.encode()
    big = b'{"id": "big", "pad": "' + b"A" * (4 << 20) + b'"}'
    cases = {
        "torn-marker": (b'{"id": "a"}\n{"id": "torn"\n' + marker[:7]
                        + b'\n{"id": "b"}\n'),
        "adjacent-torn-seals": (b'{"id": "a"}\n{"id": "torn"\n' + marker
                                + b'\n' + marker[:5] + b'\n{"id": "b"}\n'),
        "sentinel-only": marker + b"\n",
        "empty": b"",
        "crlf": b'{"id": "a"}\r\n{"id": "b"}\r\n{"id": "torn"',
        "big-record": b'{"id": "a"}\n' + big + b"\n",
    }
    for name, payload in cases.items():
        root = Path(tempfile.mkdtemp())
        backup_dir = root / "backup"
        backup_dir.mkdir()
        (backup_dir / "events.jsonl").write_bytes(payload)
        work = root / "work"
        work.mkdir()
        reader = EventLog(backup_dir / "events.jsonl")
        expected = len(reader.read_all()) + reader.torn_trailing_count
        result = restore(str(backup_dir), str(work / "r.db"),
                         events_path=str(work / "events.jsonl"))
        assert result["status"] == "ok", (name, result)
        assert result["events"] == expected, (name, result, expected)
    print("PASS test_the_backup_count_agrees_with_the_reader_on_hard_shapes")


def test_adjacent_torn_seals_are_not_counted_as_fragments():
    """Two torn seals in a row, or a torn seal before a complete one.

    A torn seal that is no longer its fragment's immediate successor is still
    an annotation: counting it refuses a replay over a harmless tear (measured).
    """
    for second in (SEAL_SENTINEL[:6], SEAL_SENTINEL):
        p = _tmp()
        log = EventLog(p)
        log.append({"id": "good-0"})
        _write_torn_tail(p, b'{"type": "PointAdded", "point": {"id": "a"')
        first = _raw(p)
        _write_torn_tail(p, b"\n" + SEAL_SENTINEL.encode() + b"\n")
        _write_torn_tail(p, b"\n" + second.encode())      # the seal tore again
        log.append({"id": "next-1"})

        reader = EventLog(p)
        records = reader.read_all()
        assert {"id": "next-1"} in records, (second, records)
        assert reader.torn_trailing_count == 1, (second, reader.torn_trailing_raw)
        assert reader.torn_tail_revival_records() == [], (
            second, "a torn seal must not be reported as an unclassifiable fragment")
        assert first  # the first state existed
    print("PASS test_adjacent_torn_seals_are_not_counted_as_fragments")


def test_an_unterminated_complete_sentinel_is_not_duplicated():
    """A seal torn AFTER its sentinel, before the sentinel's newline.

    The next append must terminate it, not write a second one (measured: the
    duplicate was journal noise, and the shape seeds the phantom-fragment
    case).
    """
    p = _tmp()
    log = EventLog(p)
    log.append({"id": "good-0"})
    _write_torn_tail(p, b'{"id": "torn"')
    # the crash lands inside the seal: terminator + sentinel reached disk, its
    # newline and the record did not
    _write_torn_tail(p, b"\n" + SEAL_SENTINEL.encode())
    log.append({"id": "next-1"})

    body = _raw(p).decode()
    assert body.count(SEAL_SENTINEL) == 1, body
    reader = EventLog(p)
    assert [r["id"] for r in reader.read_all()] == ["good-0", "next-1"], body
    assert reader.torn_trailing_count == 1, reader.torn_trailing_raw
    print("PASS test_an_unterminated_complete_sentinel_is_not_duplicated")


def test_a_copy_only_restore_of_a_corrupt_journal_still_reports():
    """`into_falkor=False` must not raise on a journal the reader refuses."""
    from tortoise.backup import restore

    root = Path(tempfile.mkdtemp())
    backup_dir = root / "backup"
    backup_dir.mkdir()
    (backup_dir / "events.jsonl").write_bytes(
        b'{"id": "a"}\n{not valid json\n{"id": "b"}\n')
    work = root / "work"
    work.mkdir()
    result = restore(str(backup_dir), str(work / "r.db"),
                     events_path=str(work / "events.jsonl"))
    assert result["status"] == "ok", result
    assert result["events"] == 3, result       # advisory line count
    print("PASS test_a_copy_only_restore_of_a_corrupt_journal_still_reports")


def test_a_cr_separated_tail_is_not_sealed():
    """`read_all` normalises universal newlines, so the seal scan must too.

    A lone-CR journal whose tail is complete records is CLEAN: annotating it
    would add a sentinel for a fragment that does not exist (measured).
    """
    p = _tmp()
    Path(p).write_bytes(b'{"id": "a"}\r{"id": "b"}\r')
    log = EventLog(p)
    log.append({"id": "NEW"})
    body = _raw(p)
    assert SEAL_SENTINEL.encode() not in body, body
    assert [r["id"] for r in EventLog(p).read_all()] == ["a", "b", "NEW"]

    # CRLF, same shape
    q = _tmp()
    Path(q).write_bytes(b'{"id": "a"}\r\n{"id": "b"}\r\n')
    EventLog(q).append({"id": "NEW"})
    assert SEAL_SENTINEL.encode() not in _raw(q), _raw(q)
    assert [r["id"] for r in EventLog(q).read_all()] == ["a", "b", "NEW"]
    print("PASS test_a_cr_separated_tail_is_not_sealed")


def test_a_terminated_malformed_cr_tail_is_sealed_and_readable():
    """A completed field-separated tear must still be marked, not left to wedge."""
    for sep in (b"\r", b"\r\n"):
        p = _tmp()
        Path(p).write_bytes(b'{"id": "a"}' + sep + b"{not json" + sep)
        log = EventLog(p)
        log.append({"id": "NEW"})
        reader = EventLog(p)
        records = reader.read_all()
        assert {"id": "NEW"} in records, (sep, records)
        assert {r["id"] for r in records} == {"a", "NEW"}, (sep, records)
        assert reader.torn_trailing_count == 1, (sep, reader.torn_trailing_raw)
    print("PASS test_a_terminated_malformed_cr_tail_is_sealed_and_readable")


class _CountingFile:
    """A file wrapper that records how many BYTES were read."""

    def __init__(self, fh):
        self._fh = fh
        self.read_bytes = 0

    def seek(self, *args):
        return self._fh.seek(*args)

    def read(self, n=-1):
        data = self._fh.read(n)
        self.read_bytes += len(data)
        return data


def test_the_window_scan_reads_each_byte_once():
    """The backwards scan must be LINEAR in the last line's length.

    The first version re-read [start, end) on every 64 KiB step: measured
    140 MB read for a 4 MiB record (next `append` 3.8 s) and 265 s for a
    32 MiB torn tail. The bound is asserted on bytes read, not on wall time,
    because the failure is an algorithmic one and the box is loaded.
    """
    size = 4 << 20
    p = _tmp()
    with open(p, "wb") as f:
        f.write(b'{"id": "big", "pad": "' + b"A" * size + b'"}\n')
    with open(p, "a+b") as f:
        counter = _CountingFile(f)
        seal = EventLog(p)._seal_prefix(counter)
    assert seal == "", seal
    assert counter.read_bytes < 2 * size, (
        f"read {counter.read_bytes} bytes to inspect a {size}-byte last line")

    # the same bound for a TORN tail (the fragment is the last line)
    q = _tmp()
    with open(q, "wb") as f:
        f.write(b'{"id": "good"}\n{"id": "torn", "pad": "' + b"B" * size)
    with open(q, "a+b") as f:
        counter = _CountingFile(f)
        seal = EventLog(q)._seal_prefix(counter)
    assert seal == "\n" + SEAL_SENTINEL + "\n", seal
    assert counter.read_bytes < 2 * size, (
        f"read {counter.read_bytes} bytes to inspect a {size}-byte fragment")
    print("PASS test_the_window_scan_reads_each_byte_once")


def test_a_small_tail_does_not_cost_a_full_window():
    """A 64 KiB floor on EVERY append is 988x read amplification (measured).

    The seal inspects the journal's last line before each append, and the
    ordinary last line is a few hundred bytes. The window starts at 4 KiB and
    doubles, so the read is bounded by 2x the LINE, independent of the
    journal's size — the old 64 KiB first window read 65,538 bytes for a
    44-byte last line however small the record was.
    """
    p = _tmp()
    with open(p, "wb") as f:
        for i in range(80000):
            f.write(b'{"id": "r%d"}\n' % i)
    size = os.path.getsize(p)
    assert size > (1 << 20), size
    with open(p, "a+b") as f:
        counter = _CountingFile(f)
        seal = EventLog(p)._seal_prefix(counter)
    assert seal == "", seal
    assert counter.read_bytes <= 2 * 4096, (
        f"read {counter.read_bytes} bytes for a {size}-byte journal whose last "
        f"line is ~15 bytes")
    print("PASS test_a_small_tail_does_not_cost_a_full_window")


def test_the_window_scan_does_not_copy_the_accumulator():
    """Linear in COPIES, not just in read bytes.

    Reading only the exposed window fixed the read volume but not
    `chunk + last_line`, which re-copies the whole accumulator every step: a
    32 MiB torn tail made the next `append` take 53 s (and 265 s before the
    read-volume fix). The bound is generous because the box is loaded; the
    failure it catches was ~50x over it.
    """
    size = 32 << 20
    p = _tmp()
    with open(p, "wb") as f:
        f.write(b'{"id": "good"}\n{"id": "torn", "pad": "' + b"C" * size)
    t0 = time.monotonic()
    EventLog(p).append({"id": "next"})
    elapsed = time.monotonic() - t0
    assert elapsed < 15, f"append took {elapsed:.1f}s over a {size}-byte fragment"
    assert [r["id"] for r in EventLog(p).read_all()] == ["good", "next"]
    print(f"PASS test_the_window_scan_does_not_copy_the_accumulator ({elapsed:.2f}s)")


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
