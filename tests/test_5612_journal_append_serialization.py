"""#5612 (item 1) — concurrent journal appends must be serialised.

The hosted lane appends to ONE per-graph journal from a bounded threadpool
(``_graph_offload`` / ``asyncio.to_thread``), so two ``append`` calls for the
same graph really do run at once. ``EventLog.append`` used to be a bare
``open(path, "a+b")`` + one ``f.write`` with the torn-tail check and the write
in an UNSERIALISED read-modify-write, and ``read_all`` refuses mid-file
corruption — so damage or a fabricated tear on that path is the failure mode
that turns "rebuildable" into "unrecoverable".

Three properties are pinned here:

1. **The write is atomic per record.** ``append`` emits ``seal + record`` with
   ONE ``write(2)`` on an ``O_APPEND`` descriptor (asserted directly, not
   inferred), which is the guarantee two writers cannot interleave INSIDE a
   record on a regular file. It is NOT the pipe ``PIPE_BUF`` rule: that bound
   is about pipes, where a write above ``PIPE_BUF`` may be split.
2. **The tail check is inside the same critical section.** Measured at base
   (12 threads x 100 appends x 6000-byte records, 8/8 runs): the unsynchronised
   ``_seal_prefix`` snapshot goes stale under concurrency and fabricates
   ``__TornTailSealed__`` annotations — a false "this journal was torn" claim
   on a healthy journal (18-31 stray seals + blank lines per run). Zero after.
3. **Recovery survives damage it did not cause.** A damaged mid-file line is
   skipped, COUNTED and reported by a salvage read instead of raising — while
   a loss that could revive removed state still refuses the replay before any
   fold (#3316). The strict read is unchanged: that contract is pinned too.

MUTATIONS THAT RED THESE (both verified):
  * revert ``append`` to ``with self.path.open("a+b") as f: f.write(...)`` ->
    ``test_concurrent_threads_cannot_interleave_or_fabricate_a_seal`` fails on
    the stray-seal assertion (the count/loss assertions still pass: CPython's
    buffered writer emits record-aligned appends, which is why the defect is
    the fabricated tear and not a lost record on a local filesystem).
  * drop the ``_OS_WRITE`` seam -> the one-syscall pin fails.
  * make ``read_all`` raise on mid-file damage again (``salvage=False`` on the
    replay engines) -> both recovery tests fail.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from tortoise.log import SEAL_SENTINEL, EventLog, TornTailResurrectionError
from tortoise.projection import InMemoryProjection

THREADS = 12
RECORDS = 100
PAD = 6000


def _record(tag: str, i: int) -> dict:
    return {"type": "PointRevised", "id": f"p{tag}", "n": i, "pad": "A" * PAD}


def test_concurrent_threads_cannot_interleave_or_fabricate_a_seal(tmp_path: Path):
    """12 threads x 100 appends: every record parses, and NOTHING is invented.

    The count assertion alone is NOT the test — it passes at base, because a
    single-handle append is record-aligned on a local filesystem. What fails at
    base is the journal's own damage annotation: a stale tail snapshot makes
    ``_seal_prefix`` seal a record it never saw torn.
    """
    path = tmp_path / "graph.jsonl"
    log = EventLog(path)
    barrier = threading.Barrier(THREADS)
    failures: list[BaseException] = []

    def writer(t: int) -> None:
        barrier.wait()
        try:
            for i in range(RECORDS):
                log.append(_record(str(t), i))
        except BaseException as exc:            # reported below
            failures.append(exc)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert failures == [], failures

    reader = EventLog(path)
    records = reader.read_all()
    assert len(records) == THREADS * RECORDS, (
        f"{len(records)} records for {THREADS * RECORDS} appends — a record was "
        f"lost or interleaved")
    assert {(r["id"], r["n"]) for r in records} == {
        (f"p{t}", i) for t in range(THREADS) for i in range(RECORDS)}

    raw = path.read_bytes()
    assert SEAL_SENTINEL not in raw.decode("utf-8"), (
        "a concurrent append fabricated a torn-tail seal on a healthy journal")
    assert b"\n\n" not in raw, "a concurrent append inserted a blank line"
    assert reader.torn_trailing_count == 0
    assert reader.corrupt_line_count == 0
    assert reader.skipped_line_count == 0


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")
def test_concurrent_processes_are_serialised_too(tmp_path: Path):
    """The same property across PROCESSES — what ``flock`` is there for.

    Threads are covered by the in-process lock; the hosted lane can also run
    more than one worker against one volume, and a filesystem whose ``O_APPEND``
    is not atomic (NFSv3, some FUSE/CSI volumes) is exactly where the write-side
    guarantee alone would not hold.
    """
    path = tmp_path / "graph.jsonl"
    nprocs, nrec = 6, 60
    pids = []
    for p in range(nprocs):
        pid = os.fork()
        if pid == 0:                            # child
            status = 1
            try:
                child_log = EventLog(path)
                for i in range(nrec):
                    child_log.append(_record(f"c{p}", i))
                status = 0
            finally:
                os._exit(status)
        pids.append(pid)
    for pid in pids:
        _, status = os.waitpid(pid, 0)
        assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0

    reader = EventLog(path)
    records = reader.read_all()
    assert len(records) == nprocs * nrec
    raw = path.read_bytes()
    assert SEAL_SENTINEL not in raw.decode("utf-8")
    assert reader.torn_trailing_count == 0 and reader.corrupt_line_count == 0


def test_one_write_syscall_per_record_on_an_append_descriptor(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The guarantee, asserted instead of assumed.

    ``append`` must hand the WHOLE record (seal included) to ONE ``write(2)``
    on a descriptor opened ``O_APPEND``. A buffered writer can split a record
    across syscalls, and that split is what a peer's append slips into.
    """
    fcntl = pytest.importorskip("fcntl")
    import tortoise.log as log_mod

    calls: list[tuple[int, bytes]] = []
    real_write = log_mod._OS_WRITE

    def spy(fd: int, data) -> int:
        calls.append((fcntl.fcntl(fd, fcntl.F_GETFL), bytes(data)))
        return real_write(fd, data)

    monkeypatch.setattr(log_mod, "_OS_WRITE", spy)

    path = tmp_path / "graph.jsonl"
    log = EventLog(path)
    for i in range(5):
        log.append({"type": "PointRevised", "id": f"p{i}"})

    assert len(calls) == 5, f"{len(calls)} write(2) calls for 5 appends"
    for flags, payload in calls:
        assert flags & os.O_APPEND, "the journal was not opened O_APPEND"
        assert payload.endswith(b"\n")
        json.loads(payload.decode("utf-8"))     # ONE complete record per write
    assert [json.loads(p.decode())["id"] for _, p in calls] == [
        f"p{i}" for i in range(5)]


def _damaged_mid_file_journal(path: Path, damaged_type: str) -> Path:
    """A journal whose MIDDLE line is a truncated record (#5612).

    Written raw, not via ``append``: a terminated malformed LAST line is sealed
    by the next append (#5917), which would make this a tolerated tear. The
    following valid record keeps the damage mid-file, where it is exactly what
    an interleave would produce.
    """
    path.write_text(
        json.dumps({"type": "PointAdded", "point": {"id": "before"}}) + "\n"
        + f'{{"type": "{damaged_type}", "point": {{"id": "damaged"' + "\n"
        + json.dumps({"type": "PointAdded", "point": {"id": "after"}}) + "\n",
        encoding="utf-8")
    return path


def test_a_salvage_read_skips_counts_and_reports_mid_file_damage(
        tmp_path: Path, caplog: pytest.LogCaptureFixture):
    """A damaged mid-file line is REPORTED and skipped, not raised (#5612)."""
    path = _damaged_mid_file_journal(tmp_path / "graph.jsonl", "PointAdded")
    reader = EventLog(path)

    with caplog.at_level("ERROR", logger="tortoise.log"):
        records = reader.read_all(salvage=True)

    assert [r["point"]["id"] for r in records] == ["before", "after"]
    assert reader.corrupt_line_count == 1
    assert reader.skipped_line_count == 1
    assert reader.corrupt_line_raw == ['{"type": "PointAdded", "point": {"id": "damaged"']
    # `PointAdded` is on the #3316 allowlist, so this loss cannot revive state:
    # the replay may proceed — with the skip counted, never silently.
    assert reader.torn_tail_revival_records() == []
    reported = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("malformed mid-file line" in m and "1 line(s) skipped" in m
               for m in reported), reported


def test_the_strict_read_still_refuses_mid_file_corruption(tmp_path: Path):
    """The #3316 reader contract is UNCHANGED for a non-recovery caller.

    Salvage is an explicit mode for the replay engines, not a new default: a
    caller that must not rebuild from an untrustworthy journal still gets the
    refusal.
    """
    path = _damaged_mid_file_journal(tmp_path / "graph.jsonl", "PointAdded")
    with pytest.raises(ValueError, match="mid-file corruption"):
        EventLog(path).read_all()


def test_recovery_continues_past_harmless_mid_file_damage(tmp_path: Path):
    """A replay engine salvages the rest; the skip is on the log object."""
    path = _damaged_mid_file_journal(tmp_path / "graph.jsonl", "PointAdded")
    log = EventLog(path)
    projection = InMemoryProjection()
    # The literal token is required by the destructive-wipe call-site lint even
    # where the backend ignores it (InMemoryProjection wipes no graph).
    projection.rebuild(log, confirm_destructive=True)
    assert sorted(projection.points) == ["after", "before"]
    assert log.corrupt_line_count == 1
    assert log.skipped_line_count == 1


def test_recovery_still_refuses_a_damaged_loss_that_could_resurrect(
        tmp_path: Path):
    """#3316 is preserved: salvage changes WHO refuses, not WHETHER.

    A ``PointRetracted`` that did not survive is not a data-LOSS direction —
    replaying the survivors would serve the removed point as current. The
    refusal still lands BEFORE any fold.
    """
    path = _damaged_mid_file_journal(tmp_path / "graph.jsonl", "PointRetracted")
    projection = InMemoryProjection()
    with pytest.raises(TornTailResurrectionError, match="proven harmless"):
        projection.rebuild(EventLog(path), confirm_destructive=True)
    assert projection.points == {}, "the fold ran despite the refusal"


def test_recover_from_log_names_the_skipped_count_in_its_refusal(tmp_path: Path):
    """The operator-facing recovery path says HOW MANY lines it skipped.

    The #3316 refusal returns before the replay touches the projection, so a
    zero-node stand-in for the store is faithful here: the only thing
    ``recover_from_log`` asks the graph pre-refusal is "how many nodes?" (an
    empty graph is the only state it auto-recovers at all).
    """
    from tortoise.consistency import recover_from_log

    class _EmptyGraph:
        def __init__(self):
            self.result_set = [[0]]

        def query(self, _cypher, params=None):
            return self

    class _EmptyProjection:
        def query(self, cypher, params=None):
            return _EmptyGraph()

    log_dir = tmp_path / "journals"
    log_dir.mkdir()
    _damaged_mid_file_journal(log_dir / "graph.jsonl", "PointRetracted")

    result = recover_from_log(str(log_dir), _EmptyProjection())
    assert result["recovered"] is False, result
    assert "1 mid-file line(s) skipped by the salvage read" in result["reason"], (
        result["reason"])
