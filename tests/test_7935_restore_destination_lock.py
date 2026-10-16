"""#7935 — two restores into ONE destination cannot destroy each other's result.

``restore`` mutates the destination across its WHOLE span: the #7928
non-empty pre-flight read, the staging renames, the journal/snapshot copy, the
replay, and the success-path aside-drop. Before #7935 the only lock in reach
was the per-RDB CONSTRUCTION lock (``embedded_lifecycle._construction_lock``,
held for ``RedisMixin.__init__`` and released when the constructor returns) plus
the SHARED owner/reaper lock — which two restores hold together. So two
restores could interleave across that whole span, and one's ROLLBACK could
discard the other's committed result: a peer that finds the destination absent
(its sibling's restore has it staged aside) records it as ``created``, and the
sibling's rollback then ``_discard``s whatever is at that path — including a
result committed there in between.

Doctrine (TEST-DOCTRINE.md, Class B) — every test's docstring answers:
  (1) what value/state makes it fail?  (2) is that value reachable in the
fixture?

These tests deliberately run the EMBEDDED lane: the destination lock is a flock
on the destination STORE's ``<db>.tortoise-construct.lock`` sidecar and the
aside protocol is filesystem-scoped, so under a ``TORTOISE_DB_URI`` (a server
graph) the destination-file assertions would be vacuous. ``TORTOISE_DB_URI`` is
deleted in the fixture for the same reason as ``test_7767_restore_rollback.py``.

Runnable:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_7935_restore_destination_lock.py -q -p no:cacheprovider
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.backup import _destination_lock, restore  # noqa: I001
from tortoise.projection.nonfolded import NonFoldedEventsError


# ── fixtures & helpers ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _embedded_lane(monkeypatch):
    """Force the embedded (file) lane — the lock and the asides are file-scoped."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)


#: Set once the process has paid its FIRST embedded construction (see below).
_WARMED = False


@pytest.fixture(autouse=True)
def _warm_first_embedded_replay(tmp_path):
    """Pay the process's FIRST embedded REPLAY outside the parked restore.

    The first payload write (a ``PointAdded`` apply) loads the embedding model,
    and under the hermetic egress guard (#4387) each unavailable fetch is retried
    with backoff — measured >60 s on a cold cache in this file. Inside a parked
    restore that cost is spent INSIDE the destination-exclusive span, so the
    gate's deadline would time the model warm-up rather than the lock. The warm-
    up runs the SAME path the tests do (a snapshot-less ``restore`` of one
    record into a scratch destination), so it cannot warm a path the tests do not
    use. Deliberately not module-scoped: it must run inside the embedded lane
    (and inside a test) to be a faithful warm-up.
    """
    global _WARMED
    if _WARMED:
        return
    scratch = tmp_path / "warmup"
    scratch.mkdir()
    src = scratch / "backup"
    src.mkdir()
    (src / "events.jsonl").write_text(
        _journal({"type": "PointAdded",
                  "point": {"id": "warm-p1", "content": "warm"}}))
    dest = scratch / "dest"
    dest.mkdir()
    result = restore(str(src), db_path=str(dest / "tortoise.db"),
                     events_path=str(dest / "events.jsonl"), into_falkor=True)
    assert result["status"] == "ok", result
    _WARMED = True


@pytest.fixture
def rdb_snapshot(tmp_path) -> bytes:
    """Bytes of a valid embedded RDB — what a real backup's snapshot is."""
    from tortoise.projection import FalkorProjection
    p = tmp_path / "snapshot-src.db"
    FalkorProjection(str(p)).close()
    return p.read_bytes()


def _journal(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def _backup_dir(root: Path, journal: str, *, snapshot: bytes | None,
                name: str = "backup") -> Path:
    """A backup directory with the given journal (and optional RDB snapshot)."""
    src = root / name
    src.mkdir()
    (src / "events.jsonl").write_text(journal)
    (src / "manifest.json").write_text(
        '{"backed_up_at":"2026-01-01","db":"tortoise.db",'
        '"events":"events.jsonl"}')
    if snapshot is not None:
        (src / "tortoise.db").write_bytes(snapshot)
    return src


def _terminalizer_miss() -> str:
    """A journal that REFUSES on every engine: a ``PointSuperseded`` whose
    target was never created (the #7767 fixture — not producible through a
    public writer, reachable from a legacy pre-journaling graph)."""
    return _journal(
        {"type": "PointAdded", "point": {"id": "p1", "content": "kept"}},
        {"type": "PointSuperseded", "id": "never-created",
         "new_id": "also-never-created", "event_id": "e-7935"},
    )


def _asides(*roots: Path) -> list[str]:
    """Every rollback aside left behind under ``roots`` (must always be [])."""
    return sorted(str(p) for root in roots for p in root.rglob("*restore-aside*"))


def _dest(root: Path) -> tuple[Path, Path]:
    d = root / "dest"
    d.mkdir()
    return d / "events.jsonl", d / "tortoise.db"


def _construct_lock_sidecar(dest_db: Path) -> Path:
    """The sidecar the store's own construction lock is taken on — the SAME file
    ``embedded_lifecycle._open_construct_lock`` opens for
    ``realpath(<dbdir>/<dbfilename>)``. Asserting on it is deliberate: the point
    of #7935 is that a restore shares the store's EXISTING writer lock rather
    than inventing a private one, so the lock's identity is part of the
    contract."""
    return Path(os.path.realpath(str(dest_db)) + ".tortoise-construct.lock")


def _flock_probe(sidecar: Path) -> None:
    """Non-blocking ``LOCK_EX`` from a fresh fd — raises ``BlockingIOError``
    while any other open file description holds the lock (``flock`` locks belong
    to the file description, so this holds across threads of one process too)."""
    fd = os.open(str(sidecar), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _restore_parked_in_its_span(events_path: str, db_path: str, backup_dir: str,
                                monkeypatch):
    """Run ``restore`` in a thread, parked INSIDE its destination-exclusive span.

    Parks at the non-folded gate — which ``restore``'s replay calls at the END
    of the journal replay, i.e. AFTER the staging renames and the copy, and
    before the rollback (on a refusing journal) or the success-path aside-drop.
    Yields ``(thread, outcome, hold)``; the caller observes the parked state and
    the context manager releases ``hold`` and joins the thread on exit.

    ``outcome`` is ``{"value": <return>}`` or ``{"error": <exception>}``; the
    thread never swallows a failure silently.
    """
    from tortoise.projection import nonfolded

    inside, hold = threading.Event(), threading.Event()
    original = nonfolded.assert_no_non_folded

    def parked(entries, **kwargs):
        inside.set()
        assert hold.wait(60), "the test's hold was never released"
        return original(entries, **kwargs)

    monkeypatch.setattr(nonfolded, "assert_no_non_folded", parked)
    outcome: dict = {}

    def _run() -> None:
        try:
            outcome["value"] = restore(str(backup_dir), db_path=db_path,
                                       events_path=events_path, into_falkor=True)
        except BaseException as exc:
            outcome["error"] = exc
        finally:
            hold.set()      # never leave the hold armed if the restore exits early

    thread = threading.Thread(target=_run, name="restore-parked")
    thread.start()
    try:
        assert inside.wait(60), "the restore never reached the replay gate"
        yield thread, outcome, hold
    finally:
        hold.set()
        thread.join(timeout=120)
        assert not thread.is_alive(), "the parked restore never finished"


# ── the lock is held across the span, and released at its end ────────────


def test_a_restore_holds_the_destination_stores_lock_across_its_span(
        tmp_path, monkeypatch):
    """#7935: FAILS IF: ``restore`` takes no destination-wide lock — asserted
    DIRECTLY, on the store's own ``<db>.tortoise-construct.lock`` sidecar, the
    lock every ``RedisMixin.__init__`` for that store already shares. Without it
    a peer restore (or a peer construction) can enter the span while this one is
    inside it, which is what makes the #7935 interleaving reachable.
    REACHABLE: an EMPTY destination plus a foldable journal, parked at the
    non-folded gate (inside the span, before the aside-drop), with the probe
    taken from the MAIN thread — a separate open file description, and ``flock``
    locks belong to the file description, so a peer fd in the SAME process must
    see EWOULDBLOCK while the restore holds it. After the restore returns the
    same probe must succeed, proving the lock is released (a leaked lock would
    wedge every later construction of the store)."""
    dest_events, dest_db = _dest(tmp_path)
    src = _backup_dir(tmp_path, _journal(
        {"type": "PointAdded", "point": {"id": "restored-p1", "content": "kept"}}),
        snapshot=None)

    with _restore_parked_in_its_span(str(dest_events), str(dest_db), str(src),
                                     monkeypatch) as (_, outcome, _hold):
        sidecar = _construct_lock_sidecar(dest_db)
        assert sidecar.exists(), (
            "no destination lock sidecar exists while the restore is inside "
            "its span (#7935)")
        with pytest.raises(BlockingIOError):
            _flock_probe(sidecar)

    assert "error" not in outcome, outcome
    assert outcome["value"]["status"] == "ok", outcome
    _flock_probe(_construct_lock_sidecar(dest_db))   # released at the span's end


# ── the filed failure: a peer restore committing inside the span ─────────


def test_a_peer_restore_cannot_commit_inside_another_restores_span(
        tmp_path, rdb_snapshot, monkeypatch):
    """#7935: FAILS IF: restore B runs to completion inside restore A's span and
    A's rollback then discards B's committed result (pre-fix, measured — the
    destination ends holding A's PRE-restore state: no journal, no DB).
    REACHABLE: an EMPTY destination; A carries the terminalizer-miss journal
    that only refuses at the END of its replay (parked there, inside the span);
    B carries a valid RDB snapshot so that B REPLACES the destination instead of
    being refused by #7928's non-empty pre-flight — without the snapshot the
    pre-flight would refuse B and the test would not reach the destruction."""
    dest_events, dest_db = _dest(tmp_path)
    miss = _backup_dir(tmp_path, _terminalizer_miss(), snapshot=None, name="a")
    good = _backup_dir(tmp_path, _journal(
        {"type": "PointAdded", "point": {"id": "b-p1", "content": "kept"}}),
        snapshot=rdb_snapshot, name="b")

    with _restore_parked_in_its_span(str(dest_events), str(dest_db), str(miss),
                                     monkeypatch) as (_, a_outcome, hold):
        b_outcome: dict = {}
        b_done = threading.Event()

        def _run_b() -> None:
            try:
                b_outcome["value"] = restore(str(good), db_path=str(dest_db),
                                            events_path=str(dest_events),
                                            into_falkor=True)
            except BaseException as exc:
                b_outcome["error"] = exc
            finally:
                b_done.set()

        b = threading.Thread(target=_run_b, name="restore-b")
        b.start()
        try:
            # THE CONTRACT: while A holds the destination, B cannot reach its own
            # destination-exclusive span, let alone commit. Pre-fix B commits
            # right here, inside A's span. Captured — not asserted — so that both
            # threads are joined before the test reports the failure.
            b_committed_inside_a = b_done.wait(2.0)
        finally:
            hold.set()          # let A's rollback run
            b.join(timeout=120)
        assert not b.is_alive(), "restore B never finished"
        assert not b_committed_inside_a, (
            "restore B completed inside restore A's span — no destination-wide "
            "lock is held (#7935)")
        assert not isinstance(a_outcome.get("error"), AssertionError), a_outcome

    # A stopped at the non-folded gate, so it must have refused...
    assert isinstance(a_outcome.get("error"), NonFoldedEventsError), a_outcome
    # ...and its ROLLBACK ran after B committed. B's result must have survived.
    assert "error" not in b_outcome, b_outcome
    assert b_outcome["value"]["status"] == "ok", b_outcome
    assert dest_events.exists(), (
        "restore A's rollback deleted the journal restore B committed")
    assert "b-p1" in dest_events.read_text(), (
        "the destination journal does not hold restore B's committed result")
    assert dest_db.exists(), "restore A's rollback deleted restore B's DB"
    assert _asides(tmp_path) == [], "rollback asides were left behind"


# ── the adjacent case: a SHARED journal, two DIFFERENT stores ────────────


def test_different_stores_still_serialise_on_a_shared_journal(
        tmp_path, rdb_snapshot, monkeypatch):
    """#7935 (review P2): FAILS IF: the destination lock owns the store but not
    the JOURNAL — two restores that share ``events_path`` while naming
    DIFFERENT stores take different locks, so B commits inside A's span and A's
    rollback then discards B's committed journal (pre-fix, measured: the shared
    ``events.jsonl`` B committed is deleted by A's rollback, because A recorded
    it as ``created`` when it staged its own copy over it).
    REACHABLE: ``tortoise restore`` defaults ``--events`` to the cwd-relative
    ``"events.jsonl"``, so two concurrent restores in one cwd into two stores
    share one journal with no exotic configuration. A carries the
    terminalizer-miss journal and parks at the non-folded gate (inside its
    span); B carries a valid RDB snapshot, so it replaces its OWN store and
    would otherwise run to completion.
    """
    shared = tmp_path / "events.jsonl"        # one journal, as the CLI defaults it
    store_a = tmp_path / "a" / "tortoise.db"
    store_b = tmp_path / "b" / "tortoise.db"
    store_a.parent.mkdir()
    store_b.parent.mkdir()
    miss = _backup_dir(tmp_path, _terminalizer_miss(), snapshot=None,
                       name="src-a")
    good = _backup_dir(tmp_path, _journal(
        {"type": "PointAdded", "point": {"id": "b-p1", "content": "kept"}}),
        snapshot=rdb_snapshot, name="src-b")

    with _restore_parked_in_its_span(str(shared), str(store_a), str(miss),
                                     monkeypatch) as (_, a_outcome, hold):
        assert shared.exists(), "A should have copied its journal into place"
        b_outcome: dict = {}
        b_done = threading.Event()

        def _run_b() -> None:
            try:
                b_outcome["value"] = restore(str(good), db_path=str(store_b),
                                             events_path=str(shared),
                                             into_falkor=True)
            except BaseException as exc:
                b_outcome["error"] = exc
            finally:
                b_done.set()

        b = threading.Thread(target=_run_b, name="restore-b")
        b.start()
        try:
            # THE CONTRACT: A's lock must cover the JOURNAL, not only its own
            # store, so B cannot commit inside A's span. Captured — not
            # asserted — so both threads join before the test reports.
            b_committed_inside_a = b_done.wait(2.0)
        finally:
            hold.set()          # let A's rollback run
            b.join(timeout=120)
        assert not b.is_alive(), "restore B never finished"
        assert not b_committed_inside_a, (
            "restore B (a different store) committed inside restore A's span — "
            "the destination lock does not own the shared journal (#7935)")
        assert not isinstance(a_outcome.get("error"), AssertionError), a_outcome

    assert isinstance(a_outcome.get("error"), NonFoldedEventsError), a_outcome
    assert "error" not in b_outcome, b_outcome
    assert b_outcome["value"]["status"] == "ok", b_outcome
    # A's rollback must NOT have discarded the journal B committed to.
    assert shared.exists(), (
        "restore A's rollback deleted the shared journal restore B committed "
        "— different stores take different locks (#7935)")
    assert "b-p1" in shared.read_text(), (
        "the shared journal does not hold restore B's committed result")
    assert store_b.exists(), "restore B's store is missing"
    assert _asides(tmp_path) == [], "rollback asides were left behind"


# ── the journal lock is not skipped when the journal IS the store ────────


def test_the_journal_lock_is_taken_even_when_the_journal_names_the_store(
        tmp_path):
    """#7935 (review P3): FAILS IF: `_destination_lock` skips the JOURNAL lock
    when `events_path` resolves to `db_path` — the pre-fix condition compared
    the two RESOURCE paths, but the two locks are DIFFERENT FILES
    (`<store>.tortoise-construct.lock` vs `<events>.restore.lock`), so no
    self-deadlock was possible and the only effect of the skip was to drop the
    journal lock in the one case it fired: a peer restoring into a DIFFERENT
    store that names this path as its journal is then not excluded.
    REACHABLE: `_destination_lock` is the seam every restore enters; the test
    enters it directly with `events_path == db_path` and probes BOTH lock files
    from fresh fds (`flock` belongs to the file description, so a peer fd in
    the same process conflicts). Pre-fix the store file is held and the journal
    file is not; post-fix both are, and both are released at the block's end.
    """
    from tortoise.embedded_lifecycle import _construct_lock_path

    store = os.path.realpath(str(tmp_path / "tortoise.db"))
    store_lock = Path(_construct_lock_path(store))
    journal_lock = Path(store + ".restore.lock")

    with _destination_lock(store, store):
        with pytest.raises(BlockingIOError):
            _flock_probe(store_lock)
        with pytest.raises(BlockingIOError):
            _flock_probe(journal_lock)

    _flock_probe(store_lock)      # both released at the block's end
    _flock_probe(journal_lock)
