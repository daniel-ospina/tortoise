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

MUTATIONS THAT RED THESE (each verified against the pre-fix code):
  * revert ``append`` to ``with self.path.open("a+b") as f: f.write(...)`` ->
    ``test_concurrent_threads_cannot_interleave_or_fabricate_a_seal`` fails on
    the stray-seal assertion (the count/loss assertions still pass: CPython's
    buffered writer emits record-aligned appends, which is why the defect is
    the fabricated tear and not a lost record on a local filesystem), and BOTH
    cross-process tests fail — the concurrent one on "the append never took
    the flock" (zero ``flock`` calls), the peer-holds-the-lock one because the
    append completes while a peer process holds it.
  * drop the ``_OS_WRITE`` seam -> the one-syscall pin fails.
  * make ``read_all`` raise on mid-file damage again (``salvage=False`` on the
    replay engines) -> both recovery tests fail.
  * treat a transient ``EINTR`` from ``flock`` as fatal -> the interruption pin
    fails with the ``OSError`` (the failure mode the lock must not add).
  * drop ``os.register_at_fork`` -> the forked child of the fork pin blocks on
    a lock its parent's other thread held at fork() time (caught by SIGALRM).
  * assume ``read(n)`` returns ``n`` bytes -> the short-read pin fabricates a
    seal on a healthy journal.
"""
from __future__ import annotations

import errno
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tortoise.log import (
    SEAL_SENTINEL,
    EventLog,
    TornTailResurrectionError,
    read_for_replay,
)
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


_CHILD = """
import json, os, sys, time, types
sys.path.insert(0, {root!r})
import tortoise.log as L
from tortoise.log import EventLog

ready_dir, cid = {ready_dir!r}, sys.argv[1]
path, nrec, pad = {path!r}, {nrec}, {pad}
calls = {{"flock": 0, "contended": 0, "degraded": 0}}
real_flock = L.fcntl.flock

def spy(fd, op):
    # Count the lock the APPEND takes, so removing it cannot pass silently.
    calls["flock"] += 1
    try:
        return real_flock(fd, op)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            calls["contended"] += 1
        raise

import errno   # noqa: E402 - after the spy so the failure path can name it
L.fcntl = types.SimpleNamespace(
    flock=spy, LOCK_EX=L.fcntl.LOCK_EX, LOCK_NB=L.fcntl.LOCK_NB)

open(os.path.join(ready_dir, "ready-" + cid), "w").close()
deadline = time.monotonic() + 120
while not os.path.exists(os.path.join(ready_dir, "go")):
    if time.monotonic() > deadline:
        sys.exit(10)          # the start barrier was never released
    time.sleep(0.005)

log = EventLog(path)
for i in range(nrec):
    log.append({{"type": "PointRevised", "id": "c" + cid, "n": i,
                 "pad": "A" * pad}})

# RENDEZVOUS: no child may exit until EVERY child has finished appending, so
# the append windows all sit inside one interval in which all of them are
# alive and appending. A driver that runs the children ONE AFTER ANOTHER (the
# vacuous shape this test used to have) cannot get past this point: the first
# child would block here and exit 11.
open(os.path.join(ready_dir, "done-" + cid), "w").close()
deadline = time.monotonic() + 120
while len([n for n in os.listdir(ready_dir) if n.startswith("done-")]) < {nprocs}:
    if time.monotonic() > deadline:
        sys.exit(11)          # the children did NOT overlap
    time.sleep(0.005)

open(os.path.join(ready_dir, "counts-" + cid), "w").write(json.dumps(
    dict(calls, degraded=len(L._FLOCK_UNHEALTHY))))
"""


def test_concurrent_processes_are_serialised_too(tmp_path: Path):
    """The same property across PROCESSES — what ``flock`` is there for.

    Threads are covered by the in-process lock; the hosted lane can also run
    more than one worker against one volume, and a filesystem whose ``O_APPEND``
    is not atomic (NFSv3, some FUSE/CSI volumes) is exactly where the write-side
    guarantee alone would not hold.

    The children must OVERLAP, which is asserted rather than assumed:

    * every child waits on a ``go`` file, so all of them are running before any
      of them appends;
    * no child exits until every child has finished appending, so all six
      append loops sit inside one common wall-clock window (a sequential driver
      wedges the first child and exits 11 -> red, not green);
    * each child REPORTS the ``flock`` calls its own appends made, so deleting
      the cross-process arm reds this test deterministically instead of relying
      on a race actually tearing a record.

    A SUBPROCESS, not ``os.fork()``: this module has already started threads, so
    a fork-then-lock child can block on a lock its parent held at fork time —
    a wedged suite instead of a failure. ``communicate(timeout=...)`` turns that
    into a red test with a message.
    """
    pytest.importorskip("fcntl")
    path = tmp_path / "graph.jsonl"
    ready = tmp_path / "barrier"
    ready.mkdir()
    nprocs, nrec = 6, 60
    root = str(Path(__file__).resolve().parent.parent)
    script = _CHILD.format(root=root, path=str(path), ready_dir=str(ready),
                           nrec=nrec, pad=6000, nprocs=nprocs)

    procs = [subprocess.Popen([sys.executable, "-c", script, str(p)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True)
             for p in range(nprocs)]
    try:
        deadline = time.monotonic() + 120
        while len(list(ready.glob("ready-*"))) < nprocs:
            assert time.monotonic() < deadline, "a child never reached the barrier"
            assert all(p.poll() is None for p in procs), (
                "a child exited before the start barrier: "
                f"{[p.returncode for p in procs]}")
            time.sleep(0.005)
        (ready / "go").write_text("")
        outcomes = [(p.communicate(timeout=300), p.returncode) for p in procs]
        outcomes = [(returncode, err) for (_, err), returncode in outcomes]
    finally:
        for p in procs:
            if p.poll() is None:            # pragma: no cover - failure path
                p.kill()
                p.communicate()
    for returncode, stderr in outcomes:
        assert returncode == 0, (returncode, stderr[-2000:])

    counts = [json.loads((ready / f"counts-{p}").read_text())
              for p in range(nprocs)]
    # A child that hit the BOUNDED wait degraded on purpose (``_FLOCK_UNHEALTHY``
    # was set, so later appends skip ``flock`` by design), and a stall is an
    # environment fact rather than a verdict on the code. Only the children that
    # did NOT degrade are held to "one flock per append" — a removal of the arm
    # can never set ``_FLOCK_UNHEALTHY``, so it still reds.
    degraded = [c["degraded"] for c in counts]
    locked = [c["flock"] for c in counts]
    if all(degraded):
        pytest.skip(f"every child degraded on a held lock: flock={locked}")
    for child, (count, was_degraded) in enumerate(
            zip(locked, degraded, strict=True)):
        if was_degraded:
            continue
        assert count >= nrec, (
            f"child {child}: {count} flock calls for {nrec} appends: the append "
            f"did not take the cross-process lock (all children: {locked})")

    reader = EventLog(path)
    records = reader.read_all()
    assert len(records) == nprocs * nrec
    raw = path.read_bytes()
    assert SEAL_SENTINEL not in raw.decode("utf-8")
    assert reader.torn_trailing_count == 0 and reader.corrupt_line_count == 0


_HOLDER = """
import fcntl, os, sys, time
fd = os.open({path!r}, os.O_CREAT | os.O_RDWR, 0o644)
fcntl.flock(fd, fcntl.LOCK_EX)
open({held!r}, "w").close()
deadline = time.monotonic() + 60
while not os.path.exists({release!r}):
    if time.monotonic() > deadline:
        sys.exit(12)
    time.sleep(0.005)
os.close(fd)
"""


def test_a_peer_process_holding_the_flock_blocks_the_append(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The cross-process arm, pinned WITHOUT depending on a race.

    A peer process takes ``flock(LOCK_EX)`` on the journal and holds it while
    this process appends. If ``append`` really serialises against other
    processes, the append cannot complete in that window; if the ``flock`` arm
    is a no-op (or gone), it completes immediately and the assertion below
    fails. This is the discrimination the concurrent test would otherwise only
    get statistically.
    """
    fcntl = pytest.importorskip("fcntl")
    import types

    import tortoise.log as log_mod

    path = tmp_path / "graph.jsonl"
    path.write_text("")
    held, release = tmp_path / "held", tmp_path / "release"
    script = _HOLDER.format(path=str(path), held=str(held), release=str(release))
    holder = subprocess.Popen([sys.executable, "-c", script])
    log = EventLog(path)
    done = threading.Event()
    refused = threading.Event()
    errors: list[BaseException] = []
    started: list[bool] = []

    # The refusal is the PIN, not the wall clock: the peer holds the lock, so the
    # append's first ``flock(LOCK_NB)`` must be REFUSED. Waiting for that event
    # cannot pass vacuously on a loaded box (the old shape could: an append that
    # had not been scheduled yet also failed to finish in 0.5 s).
    real_flock = fcntl.flock

    def refusing(fd: int, op: int) -> None:
        try:
            return real_flock(fd, op)
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                refused.set()
            raise

    monkeypatch.setattr(log_mod, "fcntl", types.SimpleNamespace(
        flock=refusing, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))

    def writer() -> None:
        try:
            log.append({"type": "PointAdded", "point": {"id": "p"}})
        except BaseException as exc:            # reported below
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=writer)
    try:
        deadline = time.monotonic() + 60
        while not held.exists():
            assert holder.poll() is None, "the peer never took the lock"
            assert time.monotonic() < deadline, "the peer never took the lock"
            time.sleep(0.005)
        thread.start()
        started.append(True)
        assert refused.wait(10), (
            "the append never contended the peer's flock — it did not take the "
            "cross-process lock at all")
        assert not done.is_set(), (
            "append completed while a peer process held the journal's flock")
    finally:
        release.write_text("")
        if started:
            thread.join(30)
        holder.wait(30)
    assert errors == [], errors
    assert done.is_set()
    assert len(EventLog(path).read_all()) == 1


def test_an_interrupted_flock_is_retried_not_raised(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A signal during the lock wait must not turn into a lost append.

    The wait polls ``flock(LOCK_NB)`` thousands of times exactly while a peer
    holds the lock, and ``fcntl.flock`` is NOT in PEP 475's retry-on-EINTR set —
    so any delivered signal can interrupt one. ``EINTR`` is transient: it is
    retried, never propagated as an ``OSError`` out of ``append`` (pre-fix it
    escaped, which is a failure class the lock itself introduced).
    """
    fcntl = pytest.importorskip("fcntl")
    import types

    import tortoise.log as log_mod

    real_flock = fcntl.flock
    interruptions = 3
    calls: list[int] = []

    def flaky(fd: int, op: int) -> None:
        calls.append(op)
        if len(calls) <= interruptions:
            raise OSError(errno.EINTR, "Interrupted system call")
        return real_flock(fd, op)

    monkeypatch.setattr(log_mod, "fcntl", types.SimpleNamespace(
        flock=flaky, LOCK_EX=fcntl.LOCK_EX, LOCK_NB=fcntl.LOCK_NB))

    path = tmp_path / "graph.jsonl"
    EventLog(path).append({"type": "PointAdded", "point": {"id": "p"}})

    assert len(calls) == interruptions + 1, calls
    assert [r["point"]["id"] for r in EventLog(path).read_all()] == ["p"]


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork only")
def test_a_forked_child_does_not_inherit_a_held_append_lock(tmp_path: Path):
    """``fork()`` while another thread appends must not wedge the child.

    ``threading.Lock`` state is COPIED into a forked child. With a journal's
    lock held by a sibling thread at ``fork()`` time, the child's copy has no
    owner and never releases — so the child's first ``append`` (the
    capture/delete path, and a prefork server's whole reason to exist) blocks
    for ever. The module reinitialises that state ``after_in_child``; without
    the hook this test hangs until the child's SIGALRM fires.
    """
    import tortoise.log as log_mod

    path = tmp_path / "graph.jsonl"
    log = EventLog(path)
    log.append({"type": "PointAdded", "point": {"id": "parent"}})

    held = log_mod._append_lock(log._lock_key())
    acquired, release = threading.Event(), threading.Event()

    def hold() -> None:
        with held:
            acquired.set()
            release.wait(30)

    holder = threading.Thread(target=hold, daemon=True)
    holder.start()
    assert acquired.wait(30), "the holder thread never took the lock"

    pid = os.fork()
    if pid == 0:                              # pragma: no cover - child process
        status = 0
        try:
            signal.alarm(10)                  # wedged -> SIGALRM ends the child
            log.append({"type": "PointAdded", "point": {"id": "child"}})
        except BaseException:
            status = 2
        os._exit(status)

    release.set()
    holder.join(30)
    _, wait_status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(wait_status) == 0, (
        "the forked child could not append — it inherited a lock held by the "
        "parent's other thread at fork() time")
    assert {"parent", "child"} == {
        r["point"]["id"] for r in EventLog(path).read_all()}


class _ShortReader:
    """An ``io.FileIO``-shaped reader that UNDER-returns from ``read(n)``.

    ``io.FileIO.read(n)`` is one ``read(2)`` and ``RawIOBase`` promises nothing
    about returning ``n`` bytes, so a short read is legal on any descriptor. The
    tail scan must read its window EXACTLY: skipping the bytes it did not get
    would silently drop the bytes that decide whether the tail is a fragment.
    """

    def __init__(self, handle, limit: int) -> None:
        self._handle = handle
        self._limit = limit

    def seek(self, *args):
        return self._handle.seek(*args)

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self._limit
        return self._handle.read(min(n, self._limit))


def test_the_tail_scan_reads_each_window_exactly(tmp_path: Path):
    """A short ``read(n)`` must not make the tail analysis mis-splice.

    ``_seal_prefix`` decides on the LAST line's bytes; a short read that skips
    the un-read remainder of the window hands it an interior fragment of the
    line, which is exactly how it can fabricate a seal on a healthy journal (or
    leave a real fragment unmarked) on the volumes this path is hardened for.
    """
    complete = json.dumps({"type": "PointAdded", "point": {"id": "a"}})
    cases = {
        "one terminated record": complete + "\n",
        "several records": complete + "\n" + complete + "\n",
        "torn tail": complete + "\n" + '{"type": "PointAdd',
        "terminated malformed": complete + "\n" + "{not json}\n",
    }
    for label, text in cases.items():
        path = tmp_path / f"graph-{label.replace(' ', '-')}.jsonl"
        path.write_text(text, encoding="utf-8")
        log = EventLog(path)
        with path.open("rb") as handle:
            expected = log._seal_prefix(handle)
        with path.open("rb") as handle:
            short = log._seal_prefix(_ShortReader(handle, 1))
        assert short == expected, f"{label}: {short!r} != {expected!r}"
    # The cases above are not vacuous: the torn tail and the terminated
    # malformed line DO need a seal, the healthy ones do not.
    several = tmp_path / "graph-several-records.jsonl"
    with several.open("rb") as handle:
        assert EventLog(several)._seal_prefix(handle) == ""


def test_the_append_resolves_the_journal_path_once(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``realpath`` is the measured cost the ``_lock_key`` memo exists for.

    The resolved key is handed to BOTH locks, so N appends cost ONE resolution —
    not one per lock per append (which is what the two locks did before the key
    was passed in).
    """
    import types

    import tortoise.log as log_mod

    calls: list[str] = []
    real_realpath = os.path.realpath

    def spy(value, *args, **kwargs):
        calls.append(os.fspath(value))
        return real_realpath(value, *args, **kwargs)

    monkeypatch.setattr(log_mod, "os", types.SimpleNamespace(
        fspath=os.fspath, realpath=spy,
        path=types.SimpleNamespace(realpath=spy)))

    path = tmp_path / "graph.jsonl"
    log = EventLog(path)
    for i in range(5):
        log.append({"type": "PointAdded", "point": {"id": f"p{i}"}})

    assert len(calls) == 1, (
        f"{len(calls)} realpath calls for 5 appends — the resolved key was "
        "recomputed on the append path")
    assert len(EventLog(path).read_all()) == 5


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


class _Result:
    def __init__(self, rows):
        self.result_set = rows


class _SyntheticStore:
    """The four calls ``recover_from_log`` makes on the success route.

    `recover_from_log` counts nodes (`query`) and folds each record (`apply`);
    nothing else on this route is store-specific, so a stand-in is faithful
    (and keeps this file hermetic — no FalkorDB, no Docker lane).
    """

    def __init__(self):
        self.nodes = 0
        self.applied: list[dict] = []

    def query(self, _cypher, params=None):
        return _Result([[self.nodes]])

    def apply(self, event):
        self.applied.append(event)
        if event.get("type") in ("PointAdded", "OperatorAdded"):
            self.nodes += 1


def test_a_successful_recovery_reports_the_skip_it_salvaged_over(tmp_path: Path):
    """`recovered: True` must not read as clean when a line was skipped.

    This is the branch an operator (and `_auto_health_recover`) reads as "the
    rebuild is fine": a salvage read that dropped a record and then replayed
    the survivors has to say so there, not only in a log line.
    """
    from tortoise.consistency import _corrupt_skip_note, recover_from_log

    log_dir = tmp_path / "journals"
    log_dir.mkdir()
    _damaged_mid_file_journal(log_dir / "graph.jsonl", "PointAdded")

    store = _SyntheticStore()
    result = recover_from_log(str(log_dir), store)
    assert result["recovered"] is True, result
    assert result["log_points"] == 2, result
    assert [e["point"]["id"] for e in store.applied] == ["before", "after"]
    assert "1 corrupt mid-file line(s) SKIPPED" in result["reason"], result["reason"]
    assert "NOT in this graph" in result["reason"], result["reason"]
    # A COUNT beside the sentence: the auto-recovery legs drop `reason` on
    # their clean line, so they branch on this key instead.
    assert result["corrupt_lines"] == 1, result

    # The sentence itself, in both shapes: one broken line reads as "the record
    # it carried", several as "the records they carried", and none is silent.
    assert _corrupt_skip_note(0) == ""
    assert "the record it carried is NOT" in _corrupt_skip_note(1)
    assert "the records they carried are NOT" in _corrupt_skip_note(3)


def test_read_for_replay_keeps_working_for_a_log_without_the_keyword(tmp_path: Path):
    """The capability is PROBED, not assumed (#5612 review).

    A replay engine also accepts synthetic log objects — a projection stub in
    the tests, a registry-only log — that only have `read_all()`. They must
    keep being read (strict), not raise `TypeError`, and a `**kwargs` reader
    must still receive `salvage=True`.
    """

    class _PreKeywordLog:
        def read_all(self):
            return [{"type": "PointAdded", "point": {"id": "stub"}}]

    assert read_for_replay(_PreKeywordLog()) == [
        {"type": "PointAdded", "point": {"id": "stub"}}]

    class _VarKeywordLog:
        def read_all(self, **kwargs):
            return [{"salvage": kwargs.get("salvage")}]

    assert read_for_replay(_VarKeywordLog()) == [{"salvage": True}]

    # And a real EventLog goes down the SALVAGE route through the same helper:
    # a damaged mid-file line is skipped here, not raised.
    log = EventLog(_damaged_mid_file_journal(tmp_path / "graph.jsonl",
                                             "PointAdded"))
    records = read_for_replay(log)
    assert [r["point"]["id"] for r in records] == ["before", "after"]
    assert log.corrupt_line_count == 1


def test_a_journal_that_is_all_damage_is_not_reported_as_empty(tmp_path: Path):
    """A non-empty journal whose every line is damaged must not read as EMPTY.

    "event log empty or unreadable — nothing to recover" invites deleting the
    one artifact that still holds the records; the salvage read knows it saw a
    damaged line, so the refusal has to say so.
    """
    from tortoise.consistency import recover_from_log

    log_dir = tmp_path / "journals"
    log_dir.mkdir()
    (log_dir / "graph.jsonl").write_text(
        '{"type": "PointAdded", "point": {"id": "one"\n'      # mid-file damage
        '{"type": "PointAdded", "point": {"id": "two"\n',     # torn tail
        encoding="utf-8")

    result = recover_from_log(str(log_dir), _SyntheticStore())
    assert result["recovered"] is False, result
    assert result["log_points"] == 0, result
    assert result["corrupt_lines"] == 1, result
    assert "NOT empty: every line failed to parse" in result["reason"], result["reason"]
    assert "1 corrupt mid-file line(s) SKIPPED" in result["reason"], result["reason"]
