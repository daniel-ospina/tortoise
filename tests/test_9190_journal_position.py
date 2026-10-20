"""`journal_event.seq` has a SOURCE on the live path (#9190, residual of #9078).

WHAT THIS PINS. `public.journal_event` keys the position a record consumed on
`(org_id, graph_id, seq)` and the schema calls `seq` *"the idempotency key"*. A
full replay derives it from the record's index (``enumerate(records, start=1)``);
before this change the LIVE path had no ordinal at all — `EventLog.append`
returned `None` — so a live write could only guess, and either guess is silent:
`MAX(seq)+1` collides with a later replay (`ON CONFLICT DO NOTHING` swallows the
replay's position row) or records one event under two seqs.

The claim is one sentence, asserted from every angle that can break it: **a
record's position is a fact of the journal** — the number a replay derives for it
from the same read — so a live write and a replay cannot number one record two
ways, and two appenders cannot both receive one number.

1. `append` reports it, and the reported position of every record equals the
   position a full replay assigns that record.
2. The reported position IS the `seq` `record_projection.replay_plan` keys the
   row on, so a live write and a replay agree on every `seq`.
3. It survives a restart from the position cache (no rescan) and is NOT believed
   when the journal changed underneath it — an append it did not see, or a
   same-length REPLACEMENT (a restore) — where it must recount instead.
4. Damage consumes no position: a sealed torn fragment and a mid-file damaged
   line add no record to a replay, so they add none to the live numbering, and
   the damaged journal is still writable.
5. Where the position is not PROVEN — the degraded lock window, a peer writing
   inside the window, an unreadable journal — `append` returns `None`, the
   record still lands, and the cache never claims a boundary nothing counted.
6. Concurrency: 8 threads appending receive `1..200` with no repeat.

NO DATABASE. The plan is materialized (`replay_plan`), never executed — the
statement set is what names the seq.
"""

from __future__ import annotations

import errno
import gc
import json
import logging
import os
import threading
import time
from pathlib import Path

import pytest

import tortoise.log as log_mod
from tortoise import record_projection as rp
from tortoise.log import EventLog, read_for_replay

#: Three record types that between them emit node, identity, relation and
#: journal statements — a realistic segment, so the seq extraction is exercised
#: across records that expand to more than one statement.
RECORDS: list[dict] = [
    {"type": "PointAdded", "ts": "2026-10-14T00:00:00Z", "id": "p1",
     "point": {"id": "p1", "content": "a claim", "pointKind": "claim"}},
    {"type": "ObjectRegistered", "ts": "2026-10-14T00:00:01Z", "id": "o1",
     "name": "a document", "objectKind": "document"},
    {"type": "PointRevised", "ts": "2026-10-14T00:00:02Z", "id": "p1",
     "new_content": "a revised claim"},
]

ORG, GRAPH = "org-1", "graph-1"


def _journal_seqs(records: list[dict], *, first_seq: int) -> list[int]:
    """The `seq` of every journal-position row a replay of *records* emits."""
    plan = rp.replay_plan(records, org_id=ORG, graph_id=GRAPH,
                          start_seq=first_seq)
    return [s.params[2] for s in plan
            if s.sql.startswith(rp._JOURNAL_INSERT_PREFIX)]


def _cache(path: Path) -> Path:
    """The sidecar the implementation keeps for *path* — beside the RESOLVED journal.

    `EventLog` keys the cache on the resolved path (so two spellings of one
    journal share it); on macOS that matters, where `/var/...` resolves to
    `/private/var/...`.
    """
    return log_mod._position_cache_path(Path(os.path.realpath(path)))


# ── the ordinal, and its agreement with a full replay ───────────────────────


def test_append_reports_the_position_a_full_replay_derives(tmp_path: Path):
    """The live ordinal is the replay's own numbering, record for record."""
    path = tmp_path / "events.jsonl"
    log = EventLog(path)

    live = [log.append(record) for record in RECORDS]

    assert live == [1, 2, 3], f"append reported {live}, not the journal positions"
    replayed = list(enumerate(read_for_replay(log), start=1))
    assert live == [seq for seq, _ in replayed], (
        "the positions the live path reported are not the positions a full "
        "replay derives — one of the two is numbering by a different rule")
    # The records themselves, so the agreement is not two empty enumerations.
    assert [record for _, record in replayed] == RECORDS
    assert log.position() == len(RECORDS)


def test_the_live_position_is_the_seq_journal_event_is_keyed_on(tmp_path: Path):
    """Live write and full replay agree on EVERY seq — no fabricated position."""
    log = EventLog(tmp_path / "events.jsonl")
    live = [log.append(record) for record in RECORDS]

    replayed = _journal_seqs(read_for_replay(log), first_seq=live[0])

    assert live == replayed == [1, 2, 3]


# ── the cache: consulted when current, recounted when not ───────────────────


def test_a_restarted_logger_continues_the_ordinal_from_the_cache(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A restart reads the cache, not the whole journal — and still numbers it."""
    path = tmp_path / "events.jsonl"
    first = EventLog(path)
    assert [first.append(r) for r in RECORDS] == [1, 2, 3]
    assert _cache(path).exists(), "the cache was never written"

    def refuse(_path: Path) -> int:
        raise AssertionError("the journal was re-counted although the cache "
                             "describes this exact file state")

    monkeypatch.setattr(log_mod, "_count_records", refuse)
    restarted = EventLog(path)

    assert restarted.position() == 3
    assert restarted.append({"type": "IngestStarted", "ts": "T"}) == 4


def test_a_record_the_cache_did_not_see_is_not_numbered_from_it(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Another writer's append makes the cache stale, and it is NOT believed.

    An older writer (one that does not maintain the sidecar), a rotated journal,
    a half-written cache — all of them change the file without changing the
    cache. The identity check turns every one of those into a RECOUNT: the
    alternative is numbering this record from a count that skipped a record,
    which is the duplicate-seq failure under a different name.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(2)] == [1, 2]
    assert _cache(path).exists()

    recount_calls: list[Path] = []
    real = log_mod._count_records

    def counting(target: Path) -> int:
        recount_calls.append(target)
        return real(target)

    monkeypatch.setattr(log_mod, "_count_records", counting)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"n": "an older writer's record"}) + "\n")

    assert EventLog(path).append({"n": 2}) == 4, (
        "the append was numbered from the stale cache instead of the journal")
    assert len(recount_calls) == 1


def test_a_rewritten_journal_of_the_same_length_is_not_believed(
        tmp_path: Path):
    """A size-preserving REPLACEMENT is a recount, not a trusted cache.

    The case a restore can produce: the journal is replaced by a DIFFERENT one
    occupying the same number of bytes. Byte size alone cannot see that, which
    is why the cache records `mtime_ns` with it — and why this test exists: it
    is the mutant that would otherwise pass, and the number it would hand out
    is one the journal no longer contains.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(4)] == [1, 2, 3, 4]
    assert _cache(path).exists()
    size = path.stat().st_size

    body = json.dumps({"n": "b0"})
    # A second record whose pad absorbs the remainder, so the replacement lands
    # on EXACTLY the same byte length as the journal it replaces (a restore can
    # do that; a size-only identity would then hand out the old count).
    fixed = len(body) + len(json.dumps({"n": ""})) + 2   # framing + 2 newlines
    pad_len = size - fixed
    assert pad_len > 0
    replacement = body + "\n" + json.dumps({"n": "p" * pad_len}) + "\n"
    assert len(replacement) == size, "the replacement must be the same size"
    before = path.stat()
    path.write_text(replacement, encoding="utf-8")
    # Deterministic, not to be relied on from timing: `mtime_ns` is a jiffy
    # (1-4 ms) on some ext4 mounts and a whole second on HFS+/FAT and many
    # NFS/FUSE exports, so a write landing inside one tick of the cached state
    # would leave the mtime UNCHANGED and make this test dependent on how fast
    # the machine is. Push the replacement past the cached tick explicitly.
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 10**9))

    assert EventLog(path).position() == 2, (
        "the cache answered for a journal it did not describe")
    assert EventLog(path).append({"n": "next"}) == 3


def test_even_a_small_journal_keeps_its_cache_current(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The cache is written THROUGH, at every size — the lag is what broke it.

    A lagging cache is worth nothing in the shape that needs it: with more than
    one writer every append moves the journal, so a cache that is not written on
    THIS append is a miss for the next one, and a miss recounts the whole journal
    inside the append's critical section. So a three-record journal must already
    carry a current sidecar, and a restart must answer from it.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append(r) for r in RECORDS] == [1, 2, 3]
    assert _cache(path).exists(), "a small journal was left uncached"

    def refuse(_path: Path) -> int:
        raise AssertionError("the cache did not describe the journal it was "
                             "written for")

    monkeypatch.setattr(log_mod, "_count_records", refuse)
    assert EventLog(path).append({"type": "IngestStarted", "ts": "T"}) == 4


def test_two_writers_in_one_process_recount_only_the_first_append(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Interleaved writers must HIT, not recount — the write-through's whole point.

    Two instances over one journal is the multi-writer shape (two processes is
    the same mechanism, pinned separately): every append by one invalidates the
    other's memo, so without a written-through cache each of them recounts the
    whole journal under the lock. With it, only the very first append pays.
    """
    path = tmp_path / "events.jsonl"
    a, b = EventLog(path), EventLog(path)

    calls: list[Path] = []
    real = log_mod._count_records

    def counting(target: Path) -> int:
        calls.append(target)
        return real(target)

    monkeypatch.setattr(log_mod, "_count_records", counting)
    issued = []
    for i in range(10):
        issued.append(a.append({"writer": "a", "i": i}))
        issued.append(b.append({"writer": "b", "i": i}))

    assert issued == list(range(1, 21))
    assert len(calls) == 1, (
        f"{len(calls)} recounts for 20 interleaved appends — the cache written "
        f"by the other writer was not used")


def test_two_spellings_of_one_journal_share_one_cache(
        tmp_path: Path):
    """A symlinked journal must not keep a second sidecar.

    The append LOCK is already keyed on the resolved path for exactly this
    reason; a cache keyed on the spelling would be written to one file and
    missed from the other, turning the optimization into a recount on the
    deployment shape the resolution exists for.
    """
    real = tmp_path / "events.jsonl"
    link = tmp_path / "linked.jsonl"
    link.symlink_to(real)

    assert EventLog(real).append({"n": 0}) == 1
    assert EventLog(link).append({"n": 1}) == 2

    sidecars = sorted(p.name for p in tmp_path.iterdir()
                      if p.name.endswith(log_mod.POSITION_SUFFIX))
    assert sidecars == ["events.jsonl" + log_mod.POSITION_SUFFIX], (
        f"the cache was keyed on the spelling, not the resolved journal: {sidecars}")


# ── damage ──────────────────────────────────────────────────────────────────


def test_a_sealed_torn_fragment_consumes_no_position(tmp_path: Path):
    """Damage is skipped by the replay, so it must be skipped by the ordinal."""
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"type": "IngestStarted", "run_id": "before"}) + "\n"
        + '{"type": "IngestStarted", "run_id": "tor' ,   # torn, unterminated
        encoding="utf-8")

    log = EventLog(path)
    assert log.append({"type": "IngestStarted", "run_id": "after"}) == 2

    records = read_for_replay(log)
    assert len(records) == 2, records
    assert _journal_seqs(records, first_seq=1) == [1, 2]


def test_a_damaged_journal_is_counted_the_way_a_replay_counts_it(tmp_path: Path):
    """A mid-file damaged line is skipped by the count because a replay skips it.

    `read_all(salvage=True)` is the replay's reader; counting with the STRICT
    read would make `append` raise `ValueError` on a journal the replay can
    still rebuild — turning a record write into a casualty of damage it can
    number around. So the count and the replay must agree on this journal.
    """
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"type": "IngestStarted", "run_id": "before"}) + "\n"
        + '{"type": "IngestStarted", "run_id": "dam' + "\n"      # mid-file damage
        + json.dumps({"type": "IngestStarted", "run_id": "after"}) + "\n",
        encoding="utf-8")

    log = EventLog(path)
    assert log.append({"type": "IngestStarted", "run_id": "ours"}) == 3
    assert _journal_seqs(read_for_replay(log), first_seq=1) == [1, 2, 3]


# ── the NAMED absence: a position that is not proven ────────────────────────


def test_an_unproven_position_is_named_and_pollutes_nothing(
        tmp_path: Path):
    """The degraded lock window yields `None` — never a number, never a cache.

    `_FLOCK_UNHEALTHY` is the documented degraded state: after one bounded wait
    a peer stops contending entirely, so one writer can hold the append lock
    while another writes inside its window. That is why the post-write byte
    offset is checked as well — and why this test sets the state the real
    timeout sets, rather than trusting the lock's `True`.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(3)] == [1, 2, 3]
    cache_before = _cache(path).read_text(encoding="utf-8")

    key = log._lock_key()
    log_mod._FLOCK_UNHEALTHY[key] = time.monotonic()   # what a timeout writes
    try:
        assert log.append({"n": "unproven"}) is None, (
            "a position was reported although the append lock was not exclusive")
    finally:
        log_mod._FLOCK_UNHEALTHY.pop(key, None)

    # The record still landed, the cache did not advance to describe it, and the
    # NEXT record's number is still the journal's own truth.
    assert len(read_for_replay(log)) == 4
    assert _cache(path).read_text(encoding="utf-8") == cache_before
    assert log.append({"n": "proven"}) == 5


def test_a_peer_writing_inside_the_window_makes_the_position_unproven(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Holding the lock is not proof — the record's own offset is.

    `append` counts, then writes. A peer that skipped the lock (the degraded
    window) can land a record between those two steps, and then the number this
    call derived belongs to the peer's record, not to this one. The post-write
    offset is the only witness, so this simulates the peer writing exactly
    there — inside our critical section, after our count.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(3)] == [1, 2, 3]
    cache_before = _cache(path).read_text(encoding="utf-8")

    real_write = log_mod._write_record

    def write_with_a_peer_inside(fd: int, payload: bytes) -> None:
        real_write(fd, json.dumps({"n": "peer"}).encode() + b"\n")
        real_write(fd, payload)

    monkeypatch.setattr(log_mod, "_write_record", write_with_a_peer_inside)
    assert log.append({"n": "ours"}) is None, (
        "a position was reported although a peer's record landed inside the window")
    # Restore ONLY this patch, by name — `monkeypatch.undo()` would revert
    # every patch the test made, including the ones the assertions below rely on.
    monkeypatch.setattr(log_mod, "_write_record", real_write)

    # Two records were appended and only one was ours: the numbering recovers
    # from the journal, not from the number this call refused to claim.
    assert len(read_for_replay(log)) == 5
    assert _cache(path).read_text(encoding="utf-8") == cache_before
    assert log.append({"n": "next"}) == 6


def test_a_journal_that_cannot_be_read_still_accepts_the_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Counting must not become an append failure the write never had.

    Before the position existed, `append` could only fail on its WRITE. A count
    that raises would hand a capture path a failure for a record it could have
    written — so an unreadable journal reports an unproven position and the
    record still lands.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"type": "IngestStarted", "run_id": "first"}) == 1

    def unreadable(_path: Path) -> int:
        raise OSError("EIO: the journal could not be read")

    monkeypatch.setattr(log_mod, "_count_records", unreadable)
    monkeypatch.setattr(log_mod, "_read_position_cache", lambda *a, **k: None)

    assert EventLog(path).append({"type": "IngestStarted", "run_id": "second"}) is None
    assert [r["run_id"] for r in read_for_replay(log)] == ["first", "second"]


# ── concurrency ─────────────────────────────────────────────────────────────


def test_two_appenders_never_receive_one_position(tmp_path: Path):
    """The ordinal rides the append's critical section, so it cannot be shared.

    Threads only: `flock`'s cross-process half of that section is pinned by
    `test_5612_journal_append_serialization.py`, and the count does not add a
    second lock to it.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    issued: list[int | None] = []
    lock = threading.Lock()
    threads: list[threading.Thread] = []

    def writer(tag: int) -> None:
        mine = [log.append({"tag": tag}) for _ in range(25)]
        with lock:
            issued.extend(mine)

    for tag in range(8):
        threads.append(threading.Thread(target=writer, args=(tag,)))
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert None not in issued, "a position was reported unproven on a free lock"
    assert sorted(issued) == list(range(1, 201))


def test_a_garbage_sidecar_is_a_recount_not_a_crash(tmp_path: Path):
    """The cache is untrusted input — and every leg of the gate is exercised.

    A malformed sidecar is the easy half. The half worth pinning is a sidecar
    that describes this journal EXACTLY (`size`/`mtime_ns` right) and still says
    something this reader must not believe: a non-integer, a bool, a negative, a
    version it does not know. Those carry a count in a shape `json` accepts, so
    only the field validation stands between them and a wrong `seq`.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(2)] == [1, 2]
    state = path.stat()

    def sidecar(records_field: str, version: str | None = None) -> str:
        """A sidecar describing THIS journal, with *records_field* substituted."""
        ver = log_mod._POSITION_VERSION if version is None else version
        return (f'{{"v":{ver},"records":{records_field},'
                f'"size":{state.st_size},"mtime_ns":{state.st_mtime_ns}}}')

    for garbage in ('{"v":2,"records":99',                            # truncated
                    sidecar("99", version="999"),                     # unknown version
                    sidecar("-4"),                                    # negative
                    sidecar("true"),                                  # bool is an int
                    sidecar('"2"'),                                   # string
                    sidecar("null"),                                  # null
                    sidecar("2.0"),                                   # float
                    "not json at all"):
        _cache(path).write_text(garbage, encoding="utf-8")
        assert EventLog(path).position() == 2, garbage

    assert EventLog(path).append({"n": "next"}) == 3
    assert len(read_for_replay(log)) == 3


def test_a_state_read_that_fails_after_the_count_costs_only_the_cache(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`position()`'s one raise means "there is no count"; a counted journal has one.

    The post-count state read (added so the cache is never written for a state the
    journal had left) is a SECOND syscall, and it can fail on the volumes this path
    is hardened for. It must then cost the cache entry and nothing else — the count
    is already in hand.
    """
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()

    calls = {"n": 0}
    real = log_mod._journal_state

    def fails_on_the_second_read(f):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(5, "EIO")
        return real(f)

    monkeypatch.setattr(log_mod, "_journal_state", fails_on_the_second_read)
    reader = EventLog(path)
    assert reader.position() == 2, (
        "a failed post-count state read cost the count")
    assert calls["n"] == 2, (
        "the post-count state read never ran, so this test proved nothing")
    assert not _cache(path).exists(), (
        "the cache was written despite an unverified state")


def test_position_refuses_to_invent_a_count_for_an_unreadable_journal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`position()` has no `None` to give, so it must raise rather than answer 0.

    Zero is a fact about an EMPTY journal; reporting it for an unreadable one
    would be the same silence this issue removes, one layer up.
    """
    path = tmp_path / "events.jsonl"
    EventLog(path).append({"n": 0})

    monkeypatch.setattr(log_mod, "_count_records",
                        lambda _p: (_ for _ in ()).throw(OSError("EIO")))
    monkeypatch.setattr(log_mod, "_read_position_cache", lambda *a, **k: None)
    with pytest.raises(OSError):
        EventLog(path).position()


def test_position_writes_the_cache_through(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`position()` is a caller's ordinary way in, so it must leave the cache current.

    Without this write, a journal whose sidecar is missing stays a MISS for
    every writer that only ever asks the position (a reader that mirrors the
    ordinal), and each of those pays an O(file) recount under the lock.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(3)] == [1, 2, 3]
    _cache(path).unlink()                 # a journal with no sidecar

    fresh = EventLog(path)
    assert fresh.position() == 3

    def refuse(_path: Path) -> int:
        raise AssertionError("the second reader re-counted: position() left "
                             "the cache stale")

    monkeypatch.setattr(log_mod, "_count_records", refuse)
    assert EventLog(path).position() == 3


def test_a_cache_of_another_version_is_not_believed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The sidecar's own format version is a gate, not decoration.

    A cache written by an older reader describes the same FILE in terms this one
    may not mean, so the version must send it to the journal — otherwise a
    change to what a field means silently keeps serving the old sense.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(3)] == [1, 2, 3]

    cached = json.loads(_cache(path).read_text(encoding="utf-8"))
    cached["v"] = log_mod._POSITION_VERSION - 1
    cached["records"] = 99               # the wrong answer, for the right file
    _cache(path).write_text(json.dumps(cached), encoding="utf-8")

    assert EventLog(path).position() == 3, (
        "a cache of another version answered for this journal")


def test_a_sidecar_that_cannot_be_opened_costs_the_cache_not_the_record(
        tmp_path: Path):
    """The descriptor is opened AFTER the record landed, so it must not raise.

    EACCES, EMFILE, a directory in the way — a cache that cannot be opened is a
    broken OPTIMIZATION, and by then the record is durable and a replay already
    counts it. Raising would tell the caller its append failed, and the retry
    would write the record twice: the duplicate this issue exists to prevent.
    `EISDIR` is used here because it is deterministic; a real deployment reaches
    the same branch through a removed sidecar replaced by a directory, a
    read-only data mount, or descriptor exhaustion.
    """
    path = tmp_path / "events.jsonl"
    assert EventLog(path).append({"n": 0}) == 1
    _cache(path).unlink()
    _cache(path).mkdir()                 # O_WRONLY on a directory: EISDIR

    log = EventLog(path)                 # no descriptor held yet: the open fails
    assert log.append({"n": 1}) == 2, "the position was lost with the cache"
    assert log.append({"n": 2}) == 3, "the failed open was not retried"
    assert [r["n"] for r in read_for_replay(log)] == [0, 1, 2]

    # ...and it RECOVERS: the failure is not memoised.
    _cache(path).rmdir()
    assert log.append({"n": 3}) == 4
    assert _cache(path).exists(), "the cache never recovered"
    assert EventLog(path).append({"n": 4}) == 5


def test_a_replaced_descriptor_detaches_its_finalizer(tmp_path: Path):
    """A finalizer must not keep a CLOSED descriptor's number.

    The descriptor is re-opened when ``self.path`` moves, and the OS reuses fd
    numbers immediately — so a finalizer still registered for the old number
    would close whichever unrelated file inherited it when the instance is
    collected. The re-key must therefore DETACH it, not leave it to fire.
    """
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    log = EventLog(first)
    assert log.append({"n": 0}) == 1
    stale = log._cache_fd_finalizer

    log.path = second                # re-key: the sidecar moves with the journal
    assert log.append({"n": 0}) == 1
    assert _cache(second).exists(), "the sidecar did not follow the journal"
    os.fstat(log._cache_fd_state[1])     # the new descriptor is open and ours

    assert not stale.alive, (
        "the finalizer for the replaced descriptor is still armed: when this "
        "instance is collected it would close whichever file inherited its number")


def test_a_failed_cache_write_costs_the_cache_not_the_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A full disk must not turn a durable record into a reported failure.

    `pwrite` raises `ENOSPC` AFTER the record has landed, so this is the exact
    shape where raising would make the caller retry and write the record twice —
    the duplicate #9190 exists to remove. Same for a closed descriptor (EBADF)
    and a read-only mount (EROFS).
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": 0}) == 1

    failing = {"on": True}
    real_pwrite = os.pwrite        # captured BEFORE the patch: `log_mod.os` IS `os`

    def pwrite_or_fail(fd: int, payload: bytes, offset: int) -> int:
        if failing["on"]:
            raise OSError(28, "No space left on device")
        return real_pwrite(fd, payload, offset)

    monkeypatch.setattr(log_mod.os, "pwrite", pwrite_or_fail)
    assert log.append({"n": 1}) == 2, "a cache write failure cost the position"
    assert log.append({"n": 2}) == 3

    assert [r["n"] for r in read_for_replay(log)] == [0, 1, 2]
    # ...and the failure is not sticky: once the disk has room, the next append
    # repairs the sidecar and the append after THAT answers from it. The
    # repairing append itself answers from the MEMO, not from a recount — the
    # memo was refreshed by every append whose cache write failed, so the stale
    # sidecar was never on the answering path. That is why the monkeypatched
    # `_count_records` below pins the append AFTER the repair: a fresh instance
    # must read the repaired sidecar rather than recount.
    failing["on"] = False
    assert log.append({"n": 3}) == 4
    monkeypatch.setattr(log_mod, "_count_records",
                        lambda _p: (_ for _ in ()).throw(
                            AssertionError("the cache was not rewritten")))
    assert EventLog(path).append({"n": 4}) == 5


def test_a_dead_sidecar_stops_the_recount_instead_of_paying_it_per_append(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An unwritable sidecar must not make every append an O(journal) read.

    The sidecar is the ONLY thing that makes the count O(1) on a journal another
    writer keeps moving. With it dead, a writer that holds no memo — one
    `EventLog` per request, the hosted shape — would otherwise read the WHOLE
    journal into memory inside the append's critical section, on EVERY append,
    on a journal that is explicitly unbounded (#9025). The measured cost is
    ~0.18 s and ~8x the journal in RSS per MiB, so the shape this replaces is
    O(n^2) on the capture path with the journal's own lock held.

    So while the path is marked dead the append REFUSES the recount and reports
    an unproven position: `None`, a named absence, never a guess — and the
    record still lands. `_count_records` is made to raise, so a recount here is
    a FAILURE rather than an unobserved extra pass.
    """
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()
    _cache(path).mkdir()                 # the sidecar cannot be written (EISDIR)

    # The FIRST append after the cache broke still pays ONE recount: the mark is
    # created by the failed write, i.e. after the count it already took.
    assert EventLog(path).append({"n": 2}) == 3

    monkeypatch.setattr(log_mod, "_count_records",
                        lambda _p: (_ for _ in ()).throw(
                            AssertionError("the journal was recounted")))
    for n in (3, 4):
        assert EventLog(path).append({"n": n}) is None, (
            "a dead sidecar put an O(journal) recount back on the append path")

    assert [r["n"] for r in read_for_replay(EventLog(path))] == [0, 1, 2, 3, 4]
    assert _cache(path).is_dir(), "the test's broken sidecar was replaced"


def test_a_dead_sidecar_is_reported_once_per_episode(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The degradation is REPORTABLE, and it does not become per-append noise.

    A DEBUG line per append is invisible to an operator, and this is not one
    missed optimization: every record to this journal now has no `seq`, which is
    the coverage the whole position mechanism exists to provide. One WARNING per
    episode is the signal; repeats stay at DEBUG, so the actionable line is not
    buried under the appends it describes.
    """
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()
    _cache(path).mkdir()

    with caplog.at_level(logging.WARNING, logger=log_mod.__name__):
        for n in range(2, 5):
            EventLog(path).append({"n": n})

    warnings = [r for r in caplog.records
                if r.levelno >= logging.WARNING
                and "cannot be written" in r.getMessage()
                and str(path) in r.getMessage()]
    assert len(warnings) == 1, (
        f"expected ONE warning for a dead sidecar, got {len(warnings)}: "
        f"{[r.getMessage() for r in warnings]}")
    assert "NO position" in warnings[0].getMessage()
    assert "30s" in warnings[0].getMessage(), (
        "the warning does not say how long the positions stay absent: "
        f"{warnings[0].getMessage()}")


def test_a_dead_sidecar_is_retried_after_its_window_and_recovers(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The mark EXPIRES, so a repaired sidecar needs no restart.

    Refusing the recount for ever would turn a one-minute permission fix into a
    permanent loss of positions. Past the window the next append recounts ONCE,
    which either proves a position and rewrites the cache or re-marks the path —
    bounded regeneration, not a sticky flag.

    The window is a real, SMALL one here (not `0.0`): with a zero window the
    "still dead" branch is unreachable and the test would pass with
    `_position_cache_dead` hard-wired to `False`.
    """
    monkeypatch.setattr(log_mod, "_POSITION_CACHE_DEAD_FOR_S", 0.4)
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()
    _cache(path).mkdir()
    assert EventLog(path).append({"n": 2}) == 3         # marks the path dead

    # INSIDE the window: the recount is refused, and that is a NAMED absence.
    assert EventLog(path).append({"n": "in-window"}) is None, (
        "a live mark did not stop the recount")

    _cache(path).rmdir()                                 # someone fixed it
    time.sleep(0.5)                                      # ...past the window
    assert EventLog(path).append({"n": 3}) in (4, 5), (
        "an expired mark still refused the recount: the positions never return")
    assert _cache(path).exists(), "the cache was not repaired by the retry"
    # ...and the mark is cleared by the successful write, not merely expired: a
    # fresh writer answers from the cache on its first append.
    monkeypatch.setattr(log_mod, "_count_records",
                        lambda _p: (_ for _ in ()).throw(
                            AssertionError("the repaired cache was not used")))
    assert EventLog(path).append({"n": 4}) in (5, 6)


def test_a_transient_failure_does_not_mark_the_sidecar_path_dead(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Only an errno that NAMES THE PATH costs the window; a blip costs a recount.

    A mark is worth 30 s of `seq`, so it must not be set by the process being
    momentarily unable to reach a perfectly good sidecar — fd-table exhaustion is
    cured by the next request ending, and `EMFILE` must therefore fail SAFE (a
    recount per append, the pre-existing cost) rather than take the positions
    away from every writer on the box.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": 0}) == 1
    _cache(path).unlink()

    real_open = log_mod._open_regular_for_write
    transient = {"on": True}

    def fail_once_then_open(target):
        if transient["on"]:
            transient["on"] = False
            raise OSError(errno.EMFILE, "Too many open files")
        return real_open(target)

    monkeypatch.setattr(log_mod, "_open_regular_for_write", fail_once_then_open)
    # A FRESH instance: `log` still holds the descriptor for the sidecar it just
    # unlinked, so it would write to the dead inode and never attempt an open.
    fresh = EventLog(path)
    assert fresh.append({"n": 1}) == 2
    assert not log_mod._position_cache_dead(fresh._lock_key()), (
        "an EMFILE — a blip about the PROCESS, not the path — marked the "
        "sidecar dead and took the positions away for the whole window")
    # ...and the very next append retries the open and repairs the cache.
    assert EventLog(path).append({"n": 2}) == 3
    assert _cache(path).exists(), "the cache was not written after the blip"


def test_a_failure_inside_the_marking_handler_cannot_fail_the_append(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The MARKING path is inside the total guard too, not just the write.

    A handler is not a safe place to assume anything: the mark is a dict insert
    and a log emission, both after the record has landed. A `MemoryError` from
    the insert (a category this module treats as expected on this path) used to
    escape from the `except OSError` handler — outside the blanket — and report
    failure for a durable record, whose retry duplicates it.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": 0}) == 1
    _cache(path).unlink()
    _cache(path).mkdir()               # a markable path failure (EISDIR)

    def boom(*_a, **_k):
        raise MemoryError("no room for the mark")

    monkeypatch.setattr(log_mod, "_mark_position_cache_dead", boom)
    fresh = EventLog(path)
    assert fresh.append({"n": 1}) == 2, (
        "a failure inside the marking handler cost the position or the record")
    assert [r["n"] for r in read_for_replay(fresh)] == [0, 1]


def test_a_dead_sidecar_warns_once_per_episode(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    """One WARNING per episode, DEBUG for the repeats.

    The mark exists because this is an operator-actionable standing condition;
    the repeats are not, and a per-append warning buries the first one.
    """
    monkeypatch.setattr(log_mod, "_POSITION_CACHE_DEAD_FOR_S", 30.0)
    path = tmp_path / "events.jsonl"
    assert EventLog(path).append({"n": 0}) == 1
    _cache(path).unlink()
    _cache(path).mkdir()

    with caplog.at_level(logging.WARNING, logger="tortoise.log"):
        for i in range(3):
            EventLog(path).append({"n": i})
    warns = [r for r in caplog.records if "cannot be written" in r.getMessage()]
    assert len(warns) == 1, f"{len(warns)} warnings for one episode"


def test_an_unproven_seal_warns_once_per_journal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog):
    """A broken mount makes `seek` fail on EVERY append; the warning must not.

    The returned `None` is the per-call signal; the WARNING is the
    operator-actionable event, and one per record buries the first one (the same
    policy `_FLOCK_UNHEALTHY` and `_POSITION_CACHE_DEAD` follow).
    """
    path = tmp_path / "events.jsonl"
    monkeypatch.setattr(EventLog, "_seal_prefix", lambda self, f: None)
    log = EventLog(path)

    with caplog.at_level(logging.WARNING, logger="tortoise.log"):
        for i in range(3):
            assert log.append({"n": i}) is None
    warns = [r for r in caplog.records if "could not be sized" in r.getMessage()]
    assert len(warns) == 1, f"{len(warns)} warnings for one persistent condition"


def test_position_recounts_and_clears_a_dead_mark(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`position()` is the documented escape hatch from the window.

    It recounts whenever it is asked (it has no `None` to give), so a sidecar
    repaired early is recognised before the window expires — otherwise the
    positions would stay away for the full 30 s after a one-second fix.
    """
    monkeypatch.setattr(log_mod, "_POSITION_CACHE_DEAD_FOR_S", 30.0)
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()
    _cache(path).mkdir()
    assert EventLog(path).append({"n": 2}) == 3          # marks it dead
    key = EventLog(path)._lock_key()
    assert log_mod._position_cache_dead(key)

    _cache(path).rmdir()                                  # fixed, inside the window
    assert EventLog(path).position() == 3                 # recounts + rewrites
    assert not log_mod._position_cache_dead(key), (
        "position() did not clear the mark although it repaired the cache")
    monkeypatch.setattr(log_mod, "_count_records",
                        lambda _p: (_ for _ in ()).throw(
                            AssertionError("the repaired cache was not used")))
    assert EventLog(path).append({"n": 3}) == 4


def test_a_re_key_whose_sidecar_cannot_be_opened_keeps_no_stale_descriptor(
        tmp_path: Path):
    """A failed re-key must forget the OLD journal's descriptor, not keep serving it.

    The instance re-points at a new journal whose sidecar cannot be opened, so
    the keys no longer match but the open fails. Keeping the old descriptor would
    write a cache that describes journal B beside journal A — a lie a later
    reader has to disbelieve on its own check, and a recount if it is lucky.
    """
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    log = EventLog(first)
    assert log.append({"n": 0}) == 1
    stale_fd = log._cache_fd_state[1]

    _cache(second).mkdir()               # B's sidecar cannot be opened
    log.path = second
    assert log.append({"n": 0}) == 1, "the failed sidecar cost the record"

    assert log._cache_fd_state is None, (
        "the descriptor of the PREVIOUS journal survived a failed re-key")
    with pytest.raises(OSError):
        os.fstat(stale_fd)               # closed, not merely dropped


def test_the_replaced_descriptor_is_really_closed(tmp_path: Path):
    """The re-key detaches the finalizer AND closes the descriptor it replaced.

    Detaching alone would leave the old descriptor open: an instance re-pointed
    at N journals would hold N-1 of them, which is the leak the kept-descriptor
    design has to avoid. Asserted through the kernel, not through a call count:
    the old fd must be GONE.
    """
    first, second = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    log = EventLog(first)
    assert log.append({"n": 0}) == 1
    stale_fd = log._cache_fd_state[1]
    os.fstat(stale_fd)                   # it is open before the re-key

    stale_ino = os.fstat(stale_fd).st_ino
    stale_target = _cache(first)

    log.path = second
    assert log.append({"n": 0}) == 1

    # The kernel hands the OLD number straight back to the next open() (measured
    # 20/20), so the number is not the evidence — what the number REFERS TO is.
    # Either the descriptor is gone (EBADF), or that number now names the NEW
    # sidecar. What must not be true is that it still names the old one.
    try:
        now_ino = os.fstat(stale_fd).st_ino
    except OSError:
        now_ino = None                   # EBADF: closed by the re-key
    assert now_ino != stale_ino or not stale_target.exists(), (
        "the descriptor still names the journal whose sidecar was replaced")


def test_counting_a_damaged_journal_does_not_report_the_damage(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """The count must not raise the alarm the REPLAY raises.

    Counting reads through the salvage reader, so a damaged journal narrates
    itself — at WARNING for a torn tail and ERROR for mid-file damage, whose text
    says the records are "NOT replayed". Emitting that from a COUNT means a
    successful `append` logs a recovery alarm, and an operator paging on ERROR
    gets a report about records that the next replay will report anyway.
    """
    path = tmp_path / "events.jsonl"
    good = json.dumps({"n": 0}) + "\n"
    path.write_text(good + "{ this is not json }\n" + json.dumps({"n": 1}) + "\n",
                    encoding="utf-8")

    with caplog.at_level(logging.DEBUG, logger="tortoise.log"):
        assert EventLog(path).append({"n": 2}) == 3
    damage = [r for r in caplog.records
              if "NOT replayed" in r.getMessage() or "SKIPPING" in r.getMessage()
              or "skipping torn" in r.getMessage()]
    assert damage == [], [r.getMessage() for r in damage]

    # The TALLY is still the count's input — and the replay still reports it.
    caplog.clear()
    reader = EventLog(path)
    with caplog.at_level(logging.ERROR, logger="tortoise.log"):
        assert len(reader.read_all(salvage=True)) == 3
    assert [r for r in caplog.records if "NOT replayed" in r.getMessage()], (
        "the replay stopped reporting the damage the count suppresses")


def test_an_unexaminable_tail_reports_no_position__and_caches_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A seal that could not be READ is not a seal that was NOT NEEDED.

    Those two states used to be the same value (`""`), and the difference is
    exactly one record: if the tail could not be examined, our record may be
    glued onto a torn fragment, the next replay skips the merged line, and the
    number this append reported belongs to a later record. Worse, the cache entry
    would describe the resulting file state EXACTLY, so nothing would ever
    recount it — a permanently off-by-one ordinal, which is the duplicate `seq`
    this issue exists to remove.
    """
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"n": 0}) + "\n" + '{"n": 1', encoding="utf-8")
    log = EventLog(path)

    real_seal = EventLog._seal_prefix
    monkeypatch.setattr(EventLog, "_seal_prefix", lambda self, f: None)
    assert log.append({"ours": 1}) is None, (
        "a position was reported for a record whose seal was never proven")

    # The record DID land (the append is not this path's to refuse) ...
    assert not _cache(path).exists(), (
        "a cache entry was written from an unproven count")
    # ... and the next writer still recounts from the journal, which numbers the
    # survivor correctly once the tail is examined normally again.
    monkeypatch.setattr(EventLog, "_seal_prefix", real_seal)
    assert EventLog(path).append({"next": 1}) == 2


def test_an_unexaminable_tail_is_reported_when_the_record_lands(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture):
    """The one state that can lose a record SILENTLY has to say so at the write.

    `_seal_prefix` returns `None` only when the tail could not be SIZED. The
    record is then written with no seal, and if the previous writer was killed
    mid-record it is glued onto that fragment: the merged line is malformed and
    FINAL, so the next replay skips BOTH and the record this call just wrote is
    absent from the replay. Nothing later can distinguish that from a clean
    write, so the loss is only ever visible at the point of the write — an
    inference from a missing record afterwards is exactly what an operator does
    not get. WARNING, once, with the record still landing.
    """
    path = tmp_path / "events.jsonl"
    path.write_text('{"n": 0', encoding="utf-8")        # a torn tail to glue onto
    log = EventLog(path)
    real_seal = EventLog._seal_prefix
    monkeypatch.setattr(EventLog, "_seal_prefix", lambda self, f: None)

    with caplog.at_level(logging.WARNING, logger=log_mod.__name__):
        assert log.append({"ours": 1}) is None, (
            "a position was reported for a record that may have been glued on")
    reported = [r for r in caplog.records if "carries NO seal" in r.getMessage()]
    assert len(reported) == 1, (
        "a possible silent record loss was not reported once: "
        f"{[r.getMessage() for r in caplog.records]}")
    assert str(path) in reported[0].getMessage()

    # A PROVEN seal says nothing: the warning is about the unexaminable tail, not
    # about appending in general.
    caplog.clear()
    monkeypatch.setattr(EventLog, "_seal_prefix", real_seal)
    with caplog.at_level(logging.WARNING, logger=log_mod.__name__):
        assert EventLog(path).append({"next": 1}) is not None
    assert not [r for r in caplog.records if "carries NO seal" in r.getMessage()]


def test_an_unexaminable_tail_is_not_reported_as_a_clean_one(tmp_path: Path):
    """The seal's own failure branch — `None`, never `""`.

    `""` asserts "this tail needs nothing", and the position relies on that. A
    seek/read that failed proves nothing, so it must be a DIFFERENT value; this
    pins the branch from the other side (the append-level test above pins what the
    caller does with it).
    """
    class _Unreadable:
        def seek(self, *_args, **_kwargs):
            raise OSError(5, "EIO")

    log = EventLog(tmp_path / "events.jsonl")
    assert log._seal_prefix(_Unreadable()) is None, (
        "an unexaminable tail was reported as a clean one")


def test_an_allocation_failure_while_remembering_the_count_keeps_the_record(
        tmp_path: Path):
    """The memo is an optimization, so its allocation must not fail the append.

    It is set right after the RECOUNT — the allocation spike — and before the
    record's write, so a `MemoryError` there would lose the record rather than
    cost a recount.
    """
    class _NoMemo(EventLog):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "_position_memo":
                raise MemoryError("no room for the memo")
            super().__setattr__(name, value)

    path = tmp_path / "events.jsonl"
    log = _NoMemo(path)
    assert log.append({"n": 0}) == 1
    assert log.append({"n": 1}) == 2
    assert [r["n"] for r in read_for_replay(log)] == [0, 1], (
        "the record was lost with the memo")


def test_a_sidecar_read_that_runs_out_of_memory_does_not_lose_the_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The sidecar read is on the append path and is not size-bounded by us.

    An unbounded sidecar (a foreign file, a runaway writer) makes `read_text`
    raise `MemoryError`, which is not an `OSError` — and BEFORE the fix it escaped
    from `_records_before` pre-write and lost the record entirely. One guard per
    promise: the count is total, so the record lands and the position is simply
    unproven.
    """
    path = tmp_path / "events.jsonl"
    assert EventLog(path).append({"n": 0}) == 1

    def boom(*_a, **_k):
        raise MemoryError("a sidecar nobody bounded")

    monkeypatch.setattr(log_mod, "_read_regular_file", boom)
    fresh = EventLog(path)               # no memo: the append must read the cache
    assert fresh.append({"n": 1}) == 2, (
        "a MemoryError from the sidecar read cost the position or the record")
    assert [r["n"] for r in read_for_replay(fresh)] == [0, 1], (
        "the record was lost with the cache read")


def test_a_special_file_at_the_cache_path_does_not_wedge_the_append(
        tmp_path: Path):
    """A FIFO at the sidecar path must be a cache MISS, not a blocking open.

    Both cache calls run INSIDE the append's critical section (thread lock plus
    `flock`), so a blocking special file there parks every appender to that
    journal in every process — with nothing distinguishing it from a wedged peer.
    A directory fails fast (EISDIR); a FIFO does not, which is why the open is
    non-blocking and the result must be a REGULAR file.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": 0}) == 1

    _cache_file = _cache(path)
    _cache_file.unlink()
    os.mkfifo(_cache_file)                       # readers/writers block

    # The read side must not block; the write side must fail fast (ENXIO).
    assert log.append({"n": 1}) == 2, "the append blocked on a FIFO sidecar"

    # ...and a fresh instance's READ must not block either. Its position is
    # proven by ONE recount (taken before the failed write marks the path).
    assert EventLog(path).append({"n": 2}) == 3, (
        "a fresh writer's read blocked on, or lost its position to, a FIFO")
    # The next fresh writer is in the `_POSITION_CACHE_DEAD` window the FIFO
    # opened, so it reports a NAMED ABSENCE instead of an O(journal) recount
    # (see `test_a_dead_sidecar_stops_the_recount_instead_of_paying_it_per_append`).
    assert EventLog(path).append({"n": 3}) is None, (
        "a fresh writer paid a recount against a sidecar the FIFO made dead")
    assert [r["n"] for r in read_for_replay(EventLog(path))] == [0, 1, 2, 3]


def test_a_fifo_with_a_reader_costs_the_cache_not_the_record(tmp_path: Path):
    """The unprivileged FIFO shape: `open` SUCCEEDS, the write does not.

    `open` refuses a FIFO nobody is reading (ENXIO) and a directory (EISDIR), so
    the plain FIFO test never gets past the open. With a reader holding it, the
    open succeeds and the failure arrives at the `pwrite` instead — one layer
    further down, still inside the single blanket guard, and still costing only
    the cache.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": 0}) == 1
    _cache(path).unlink()
    os.mkfifo(_cache(path))
    log._cache_fd_state = None              # force a fresh open, past the kept fd

    reader = os.open(_cache(path), os.O_RDONLY | os.O_NONBLOCK)
    try:
        assert log.append({"n": 1}) == 2, "the non-regular sidecar cost the position"
    finally:
        os.close(reader)
    assert [r["n"] for r in read_for_replay(log)] == [0, 1]


def test_a_symlinked_sidecar_is_neither_read_nor_written_through(
        tmp_path: Path):
    """`O_NOFOLLOW`, on BOTH sides, asserted rather than promised.

    The payload is only JSON, but "read a count from, and write a count to,
    wherever a link points" is the primitive nobody should get by dropping a
    symlink into a data directory — and it is the one shape where a cache can
    invent a count out of another file's contents. So the victim holds a
    perfectly VALID cache payload claiming 99 records for this journal's exact
    file state: with `O_NOFOLLOW` the link is refused and the journal is counted
    (3); without it, the 99 is believed.
    """
    path = tmp_path / "events.jsonl"
    victim = tmp_path / "victim.txt"
    log = EventLog(path)
    assert [log.append({"n": 0}), log.append({"n": 1})] == [1, 2]
    state = path.stat()

    victim.write_text(json.dumps({"v": log_mod._POSITION_VERSION,
                                  "records": 99,
                                  "size": state.st_size,
                                  "mtime_ns": state.st_mtime_ns}),
                      encoding="utf-8")
    _cache(path).unlink()
    _cache(path).symlink_to(victim)
    before = victim.read_text(encoding="utf-8")

    # A FRESH instance, so nothing is memoised and the sidecar is really read.
    assert EventLog(path).append({"n": 2}) == 3, (
        "the count came from (or the write went through) a symlinked sidecar")
    assert victim.read_text(encoding="utf-8") == before, (
        "the cache write followed a symlink")
    assert len(read_for_replay(EventLog(path))) == 3


def test_position_does_not_cache_a_count_taken_on_a_moved_journal(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`position()` has no record to check its offset against, so it re-reads the state.

    `append` proves exclusivity with its own record's byte offset. `position()`
    counts without appending, so a peer in the degraded window can move the
    journal DURING the count — and caching `(count, size, mtime_ns)` from the
    state before it would assert that this exact file state held a count it never
    held. A restore to that state would then serve the lie as a fact, with no
    later writer able to make it miss.
    """
    path = tmp_path / "events.jsonl"
    assert [EventLog(path).append({"n": i}) for i in range(2)] == [1, 2]
    _cache(path).unlink()

    real_count = log_mod._count_records

    def count_then_peer_writes(target: Path) -> int:
        counted = real_count(target)
        # A peer that does NOT contend — the `_FLOCK_UNHEALTHY` shape, and the
        # only way to move the journal from inside this thread (a real `append`
        # here would re-enter this journal's own thread lock).
        with open(target, "a", encoding="utf-8") as f:
            f.write(json.dumps({"peer": 1}) + "\n")
        return counted

    monkeypatch.setattr(log_mod, "_count_records", count_then_peer_writes)
    reader = EventLog(path)                       # fresh: the sidecar is READ
    assert reader.position() == 2

    assert not _cache(path).exists(), (
        "a cache entry was written for a state the journal had already left")


def test_a_shrinking_cache_write_leaves_no_tail(tmp_path: Path):
    """`pwrite` overwrites but does not truncate, so a shorter payload needs the ftruncate.

    Every field here is a number whose WIDTH varies (a count crossing 9→10, a
    size crossing a power of ten, an `mtime_ns`). Without the truncate the
    shorter payload leaves the longer one's tail behind, the result is not JSON,
    and the cache becomes PERMANENTLY unreadable — a recount on every append,
    which is the cost the cache exists to avoid.
    """
    cache = tmp_path / "events.jsonl.position"
    fd = os.open(cache, os.O_WRONLY | os.O_CREAT, 0o644)
    try:
        log_mod._write_position_cache(fd, 999_999, 1 << 40, 1_700_000_000_000_000_000)
        log_mod._write_position_cache(fd, 2, 9, 7)
    finally:
        os.close(fd)

    assert json.loads(cache.read_text(encoding="utf-8")) == {
        "v": log_mod._POSITION_VERSION, "records": 2, "size": 9, "mtime_ns": 7}


def test_one_descriptor_serves_every_cache_write(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The kept descriptor is the reason write-through is affordable.

    Write-through is only viable because the cache costs a `pwrite`+`ftruncate`
    on a descriptor already open (~8 us) instead of open/write/close (~114 us) —
    so an implementation that opened one per append would pay a 50-100% tax on
    every append, and one that opened without closing would leak a descriptor per
    write. Both are pinned here: N appends, ONE open, and it is closed when the
    instance is released.
    """
    path = tmp_path / "events.jsonl"
    opens: list[str] = []
    sidecar_fds: list[int] = []       # the fds THIS test's filtered opens returned
    closes: list[int] = []
    real_open, real_close = os.open, os.close

    def counting_open(target, *args, **kwargs):
        # Filtered to WRITE opens of the SIDECAR: `log_mod.os` IS `os`, so an
        # unfiltered counter would also collect every other open() in the
        # interpreter during this test and make the assertion flake under a
        # concurrent fixture or a teammate's `__del__`. The read side opens the
        # sidecar too (once per instance, which is what the memo then answers
        # for), and this test is about the KEPT write descriptor.
        flags = args[0] if args else kwargs.get("flags", 0)
        if (os.fspath(target).endswith(log_mod.POSITION_SUFFIX)
                and flags & os.O_WRONLY):
            opens.append(os.fspath(target))
            fd = real_open(target, *args, **kwargs)
            sidecar_fds.append(fd)
            return fd
        return real_open(target, *args, **kwargs)

    def counting_close(fd: int, *args, **kwargs):
        closes.append(fd)
        return real_close(fd, *args, **kwargs)

    monkeypatch.setattr(log_mod.os, "open", counting_open)
    monkeypatch.setattr(log_mod.os, "close", counting_close)

    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(5)] == [1, 2, 3, 4, 5]
    assert len(opens) == 1, f"{len(opens)} opens for 5 appends: the descriptor " \
                            f"was not kept"
    # Intersected with the SIDECAR fds, not `closes == []`: `closes` collects
    # every `os.close` in the process (`log_mod.os` IS `os`), so an unrelated
    # file object closed by a fixture, another thread or a finalizer during this
    # test would fail it for the wrong reason — and the failure would name a
    # kept descriptor that was never closed.
    assert not (set(sidecar_fds) & set(closes)), (
        "the descriptor was closed while the log was in use")

    held = log._cache_fd_state[1]
    del log
    gc.collect()
    assert held in closes, "the descriptor outlived the instance: a leak in the " \
                           "per-request shape"


def test_position_reports_a_stat_failure_as_the_same_oserror(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The fstat is the FIRST thing `position()` does, and it can fail too.

    Documented as one contract (`:raises OSError:`) while being two syscalls, so
    an escaped bare `OSError` from the `fstat` would be indistinguishable from
    the count's — and a caller guarding on the message would miss it.
    """
    path = tmp_path / "events.jsonl"
    EventLog(path).append({"n": 0})

    def refuse(_f) -> tuple[int, int]:
        raise OSError("EIO")

    monkeypatch.setattr(log_mod, "_journal_state", refuse)
    with pytest.raises(OSError, match="cannot stat the journal"):
        EventLog(path).position()


_CHILD = """
import json, sys
sys.path.insert(0, {root!r})
from tortoise.log import EventLog

log = EventLog({path!r})
print(json.dumps([log.append({{"child": sys.argv[1], "n": i}})
                  for i in range({n})]))
"""


def test_two_processes_never_receive_one_position(tmp_path: Path):
    """The ordinal across a real `flock`, which threads do not exercise.

    The issue's failure is a live writer inventing a number a replay then
    disagrees with; two processes appending at once is the shape that produces
    it, and the lock the position rides is cross-process (`flock`), not just
    the in-process thread lock.
    """
    import subprocess
    import sys

    path = tmp_path / "events.jsonl"
    root = str(Path(__file__).resolve().parent.parent)
    script = _CHILD.format(root=root, path=str(path), n=30)
    procs = [subprocess.Popen([sys.executable, "-c", script, tag],
                              stdout=subprocess.PIPE, text=True)
             for tag in ("a", "b")]
    issued: list[int | None] = []
    for proc in procs:
        out, _ = proc.communicate(timeout=120)
        assert proc.returncode == 0
        issued.extend(json.loads(out))

    assert None not in issued, "a position was reported unproven on a free lock"
    assert sorted(issued) == list(range(1, 61)), (
        "the two processes did not divide the journal's positions between them")
    assert len(read_for_replay(EventLog(path))) == 60


def test_a_failing_state_read_costs_the_position_not_the_record(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`fstat` fails too (ESTALE/EIO), and neither failure may cost a record.

    PRE-write there is no state to count from or to verify against, so the
    position is unproven — but the append must still happen. POST-write the
    record has ALREADY landed, so raising there would tell the caller a write
    failed that succeeded, and the caller's retry would append it a second time
    under a different `seq` — the duplicate this issue exists to remove.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert log.append({"n": "first"}) == 1
    real = log_mod._journal_state

    def always_fails(_f) -> tuple[int, int]:
        raise OSError("EIO")

    monkeypatch.setattr(log_mod, "_journal_state", always_fails)
    assert log.append({"n": "pre"}) is None
    assert [r["n"] for r in read_for_replay(log)] == ["first", "pre"]

    calls = {"n": 0}

    def fails_on_the_second_call(f) -> tuple[int, int]:
        calls["n"] += 1
        if calls["n"] == 2:                 # the post-write read
            raise OSError("EIO")
        return real(f)

    monkeypatch.setattr(log_mod, "_journal_state", fails_on_the_second_call)
    assert log.append({"n": "post"}) is None
    assert [r["n"] for r in read_for_replay(log)] == ["first", "pre", "post"]


def test_position_does_not_re_read_the_journal_on_every_call(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A count a caller paid for is remembered — the memo must be REFRESHED.

    A journal with no usable cache is the worst shape for a poller: nothing but
    the memo stands between `position()` and an O(file) read of the journal on
    every call, with the append lock held for it. So the miss path must install
    the answer it derived (`_position_memo`), not only read it.
    """
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    assert [log.append({"n": i}) for i in range(3)] == [1, 2, 3]
    # Force the MISS path: `position()` has to recount the journal here, and
    # whatever it derives is what the next two calls must reuse. The whole
    # refresh is disabled with it — otherwise the write-through would answer the
    # second and third calls and the MEMO would never be exercised (a reflex that
    # hid a broken memo when this was first written).
    _cache(path).unlink()
    monkeypatch.setattr(EventLog, "_refresh_position_cache",
                        lambda *a, **k: None)

    calls: list[Path] = []
    real = log_mod._count_records

    def counting(target: Path) -> int:
        calls.append(target)
        return real(target)

    monkeypatch.setattr(log_mod, "_count_records", counting)
    fresh = EventLog(path)          # a restart: no memo yet
    assert [fresh.position() for _ in range(3)] == [3, 3, 3]
    assert len(calls) == 1, f"{len(calls)} recounts for 3 position() calls"
