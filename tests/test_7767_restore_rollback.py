"""#7767 — a REFUSED ``backup.restore`` must leave the destination intact.

``restore(..., into_falkor=True)`` replaces the destination journal — and, when
the backup carries a snapshot, the destination DB *and* its AOF dir — BEFORE
the replay loop that can refuse (``NonFoldedEventsError``, R8) or fail (a
malformed mid-file journal line, a torn backend, an ``OSError`` mid-copy). The
torn-tail verdict is taken earlier still (#3316), but every OTHER refusal shape
is only decidable by replaying, so a refusal used to land on a store that had
already been destroyed, with no rollback: the caller got the exception AND a
mutated destination.

Doctrine (TEST-DOCTRINE.md, Class B) — every test's docstring answers:
  (1) what value/state makes it fail?  (2) is that value reachable in the
fixture?

These tests deliberately run the EMBEDDED lane: the rollback is about
*filesystem* destinations, and under a ``TORTOISE_DB_URI`` a
``FalkorProjection(path)`` redirects to a server graph and touches no file, so
the file assertions would be vacuous. ``TORTOISE_DB_URI`` is deleted in the
fixture for the same reason.

Runnable:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \\
    tests/test_7767_restore_rollback.py -q -p no:cacheprovider
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.backup import restore  # noqa: I001
from tortoise.projection import remove_stale_aof, stale_aof_dirs
from tortoise.projection.nonfolded import NonFoldedEventsError


# ── fixtures & helpers ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _embedded_lane(monkeypatch):
    """Force the embedded (file) lane — the rollback is filesystem-scoped."""
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)


@pytest.fixture
def rdb_snapshot(tmp_path) -> bytes:
    """Bytes of a valid embedded RDB — what a real backup's snapshot is.

    A freshly-constructed projection always carries at least one node, so
    ``restore``'s RDB-first probe is truthy and takes the early return (no
    replay). Used by the tests that need the SNAPSHOT-present path.
    """
    from tortoise.projection import FalkorProjection
    p = tmp_path / "snapshot-src.db"
    FalkorProjection(str(p)).close()
    return p.read_bytes()


def _journal(*records: dict) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


def _backup_dir(root: Path, journal: str, *, snapshot: bytes | None) -> Path:
    """A backup directory with the given journal (and optional RDB snapshot)."""
    src = root / "backup"
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
    target was never created. Not producible through a public writer (each
    guards its target) — reachable from a legacy pre-journaling graph or an
    unjournaled producer."""
    return _journal(
        {"type": "PointAdded", "point": {"id": "p1", "content": "kept"}},
        {"type": "PointSuperseded", "id": "never-created",
         "new_id": "also-never-created", "event_id": "e-7767"},
    )


def _asides(*roots: Path) -> list[str]:
    """Every rollback aside left behind under ``roots`` (must always be [])."""
    return sorted(str(p) for root in roots for p in root.rglob("*restore-aside*"))


def _dest(root: Path) -> tuple[Path, Path]:
    d = root / "dest"
    d.mkdir()
    return d / "events.jsonl", d / "tortoise.db"


# ── the refusal must not have replaced anything ──────────────────────────


def test_a_refused_restore_leaves_the_destination_journal_byte_identical(
        tmp_path):
    """FAILS IF: the destination journal is overwritten before the replay can
    refuse (the #7767 defect — pre-fix `after == before` was False, measured).
    REACHABLE: a destination journal plus a backup whose ``events.jsonl``
    carries a terminalizer miss and NO RDB snapshot (the JSONL fallback, which
    is where the refusal is decidable at all)."""
    dest_events, dest_db = _dest(tmp_path)
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-p1"}}')
    src = _backup_dir(tmp_path, _terminalizer_miss(), snapshot=None)
    before = dest_events.read_bytes()

    with pytest.raises(NonFoldedEventsError):
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert dest_events.read_bytes() == before, (
        "the refused restore replaced the destination journal (#7767)")
    assert not dest_db.exists(), "the replay's partial DB was left behind"
    assert _asides(tmp_path) == [], "rollback asides were left behind"


def test_a_refused_restore_does_not_create_a_destination_it_lacked(tmp_path):
    """FAILS IF: a destination that did NOT exist before the restore is left
    behind by the failed replay (a half-populated DB the caller never asked
    for). REACHABLE: empty destination dir + the same terminalizer-miss
    backup."""
    dest_events, dest_db = _dest(tmp_path)
    src = _backup_dir(tmp_path, _terminalizer_miss(), snapshot=None)

    with pytest.raises(NonFoldedEventsError):
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert not dest_events.exists(), "a journal was created and left behind"
    assert not dest_db.exists(), "a DB was created and left behind"
    assert _asides(tmp_path) == [], "rollback asides were left behind"


def test_a_malformed_mid_file_journal_rolls_the_destination_back(tmp_path):
    """FAILS IF: the rollback is wired to `NonFoldedEventsError` specifically
    instead of to ANY failure — the window is "a verdict decided by replaying",
    not the one exception class that names it today. REACHABLE: a MID-FILE
    malformed line (a torn TRAILING line is tolerated by the reader, so the bad
    line is followed by a valid one) makes `EventLog.read_all` raise `ValueError`
    after the copy and before the replay."""
    dest_events, dest_db = _dest(tmp_path)
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-p1"}}')
    before = dest_events.read_bytes()
    malformed = (
        '{"type":"PointAdded","point":{"id":"p1","content":"kept"}}\n'
        "{not json at all\n"
        '{"type":"PointAdded","point":{"id":"p2","content":"kept"}}\n'
    )
    src = _backup_dir(tmp_path, malformed, snapshot=None)

    with pytest.raises(ValueError):
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert dest_events.read_bytes() == before, (
        "a non-refusal failure left the destination journal replaced")
    assert not dest_db.exists()
    assert _asides(tmp_path) == [], "rollback asides were left behind"


def test_a_successful_restore_commits_and_leaves_no_aside(tmp_path):
    """FAILS IF: the rollback fires on SUCCESS — a false rollback would undo a
    legitimate restore, and a staged aside that is never dropped would leak a
    copy of the journal next to the destination. REACHABLE: a foldable journal
    (one plain PointAdded) and no snapshot."""
    dest_events, dest_db = _dest(tmp_path)
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-p1"}}')
    src = _backup_dir(
        tmp_path,
        _journal({"type": "PointAdded",
                  "point": {"id": "restored-p1", "content": "kept"}}),
        snapshot=None)

    result = restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                     into_falkor=True)

    assert result["status"] == "ok", result
    assert result["events"] == 1, result
    assert "restored-p1" in dest_events.read_text(), (
        "a successful restore must COMMIT the journal")
    assert dest_db.exists(), "a successful restore must keep the replayed DB"
    assert _asides(tmp_path) == [], "a committed restore left an aside behind"


# ── the snapshot-less fold must not land on a populated graph (#7928) ──


def test_a_snapshot_less_restore_refuses_a_populated_destination_before_mutating(
        tmp_path):
    """#7928: FAILS IF: a snapshot-less restore folds the journal INTO a
    destination graph that already holds nodes, so a refusal that lands
    mid-replay leaves the partially-folded records behind (pre-fix the
    destination held BOTH `pre-existing` and `added-by-partial-replay`,
    measured).
    REACHABLE: a destination DB holding one Point and a backup with NO
    snapshot whose journal is a terminalizer miss that only refuses AFTER the
    earlier `PointAdded` has been folded — the exact shape filed on #7928."""
    from tortoise.backup import NonEmptyDestinationError
    from tortoise.projection import FalkorProjection

    dest_events, dest_db = _dest(tmp_path)
    # Create the destination GRAPH first (so its node set is exactly
    # `pre-existing`) and only then give it a journal — a journal written
    # before the DB exists is auto-folded into the DB on first open, which is
    # not the state under test.
    keeper = FalkorProjection(str(dest_db))
    try:
        keeper.g.query("CREATE (:Point {id: 'pre-existing', content: 'kept'})")
    finally:
        keeper.close()
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-marker"}}')
    before = dest_events.read_bytes()
    src = _backup_dir(
        tmp_path,
        _journal(
            {"type": "PointAdded",
             "point": {"id": "added-by-partial-replay", "content": "x"}},
            {"type": "PointSuperseded", "id": "never", "new_id": "y",
             "event_id": "e-7928"},
        ),
        snapshot=None)

    with pytest.raises(NonEmptyDestinationError) as exc:
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert exc.value.node_count >= 1, exc.value
    assert dest_events.read_bytes() == before, (
        "the refused restore replaced the destination journal")
    proj = FalkorProjection(str(dest_db))
    try:
        ids = sorted(r[0] for r in
                     proj.g.query("MATCH (n:Point) RETURN n.id").result_set)
    finally:
        proj.close()
    assert ids == ["pre-existing"], (
        f"the refused restore left a partial fold in the destination: {ids}")
    assert _asides(tmp_path) == [], "rollback asides were left behind"


def test_a_snapshot_less_restore_still_folds_into_an_absent_destination(
        tmp_path):
    """FAILS IF: the #7928 pre-flight refuses a destination that does not
    exist — the happy path the snapshot-less JSONL fallback exists for.
    REACHABLE: an absent destination DB and a foldable journal."""
    dest_events, dest_db = _dest(tmp_path)
    src = _backup_dir(
        tmp_path,
        _journal({"type": "PointAdded",
                  "point": {"id": "restored-p1", "content": "kept"}}),
        snapshot=None)

    result = restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                     into_falkor=True)

    assert result["status"] == "ok", result
    assert dest_db.exists(), "a fresh snapshot-less restore must keep its DB"


def test_a_snapshot_restore_still_replaces_a_populated_destination(
        tmp_path, rdb_snapshot):
    """FAILS IF: the #7928 pre-flight refuses a restore whose backup carries a
    snapshot — the destination is REPLACED on that path, so a populated one is
    not the fold-into hazard the refusal guards.
    REACHABLE: a destination DB plus a backup carrying a valid snapshot
    (RDB-first, so no replay and no fold)."""
    dest_events, dest_db = _dest(tmp_path)
    dest_db.write_bytes(b"STALE-DB-BYTES")
    src = _backup_dir(tmp_path, "[]\n", snapshot=rdb_snapshot)

    result = restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                     into_falkor=True)

    assert result["status"] == "ok", result
    assert result.get("restored_via") == "rdb", result


# ── the snapshot-present path ────────────────────────────────────────────


def test_a_successful_restore_with_a_snapshot_drops_the_stale_aof_dir(
        tmp_path, rdb_snapshot):
    """FAILS IF: staging the destination's AOF dir makes `remove_stale_aof` a
    no-op that also never drops the aside, so a COMPLETED restore leaves the old
    AOF to shadow the copied snapshot (#915's whole point) — or leaves an aside
    behind. REACHABLE: a destination with an AOF dir and a backup carrying a
    valid snapshot (the RDB path, so the restore commits without replaying)."""
    dest_events, dest_db = _dest(tmp_path)
    dest_db.write_bytes(b"STALE-DB-BYTES")
    aof = Path(str(dest_db) + "-appendonlydir")
    aof.mkdir()
    (aof / "appendonly.aof").write_text("stale")
    src = _backup_dir(tmp_path, "[]\n", snapshot=rdb_snapshot)

    result = restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                     into_falkor=True)

    assert result["status"] == "ok", result
    assert result.get("restored_via") == "rdb", result
    # The RDB is NOT asserted byte-identical: the probe opens the copied
    # snapshot, and closing that server rewrites the RDB. What matters is that
    # the destination no longer holds the stale store.
    assert dest_db.read_bytes().startswith(b"REDIS"), (
        "the destination DB must hold the copied snapshot")
    assert dest_db.read_bytes() != b"STALE-DB-BYTES", "the snapshot must win"
    assert not aof.exists(), "the stale AOF dir must not survive a restore"
    assert _asides(tmp_path) == [], "a committed restore left an aside behind"


def test_a_torn_snapshot_rolls_back_the_journal_db_and_both_aof_dirs(
        tmp_path, rdb_snapshot, monkeypatch):
    """FAILS IF: the destination DB overwrite, or the AOF rmtree, is not
    rolled back when the copied snapshot cannot be OPENED — the injected/torn
    backend case option (b) on #7767 exists for, and the ONLY way the
    snapshot-present path reaches a failure (a readable snapshot takes the RDB
    early return, so no replay and no refusal). REACHABLE: a backup carrying a
    snapshot plus a backend that raises on construction; the destination holds
    bytes no engine can parse, deliberately — it is staged aside before the
    copy, so a fix that stopped staging it would fail here instead of passing."""
    import tortoise.projection as proj_mod

    class _TornSnapshot:
        def __init__(self, *a, **k):
            raise ConnectionError("torn snapshot: cannot open the RDB (#7767)")

    monkeypatch.setattr(proj_mod, "FalkorProjection", _TornSnapshot)

    dest_events, dest_db = _dest(tmp_path)
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-p1"}}')
    dest_db.write_bytes(b"ORIGINAL-DB-BYTES")
    per_db_aof = Path(str(dest_db) + "-appendonlydir")
    legacy_aof = tmp_path / "dest" / "appendonlydir"
    for d, payload in ((per_db_aof, "ORIGINAL-AOF"), (legacy_aof, "ORIGINAL-LEGACY")):
        d.mkdir()
        (d / "appendonly.aof").write_text(payload)
    src = _backup_dir(tmp_path, "[]\n", snapshot=rdb_snapshot)
    before = dest_events.read_bytes()

    with pytest.raises(ConnectionError):
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert dest_events.read_bytes() == before, "the journal was not restored"
    assert dest_db.read_bytes() == b"ORIGINAL-DB-BYTES", "the DB was not restored"
    assert (per_db_aof / "appendonly.aof").read_text() == "ORIGINAL-AOF", (
        "the per-DB AOF dir was not restored")
    assert (legacy_aof / "appendonly.aof").read_text() == "ORIGINAL-LEGACY", (
        "the legacy AOF sibling was not restored")
    assert _asides(tmp_path) == [], "rollback asides were left behind"


def test_a_staging_failure_rolls_back_what_was_already_staged(
        tmp_path, rdb_snapshot, monkeypatch):
    """FAILS IF: the destination staging renames sit OUTSIDE the guarded
    region, so a failure on a LATER stage escapes before any rollback runs —
    the journal has already been renamed aside and is never put back, which is
    the #7767 shape surviving in the staging phase (found in review, and
    reproduced there with an injected ``os.replace``).
    REACHABLE: a destination holding BOTH a journal and a DB, with the DB's
    rename made to fail — ``events_path`` and ``db_path`` are routinely
    different paths, so a failure on one must not strand the other. The DB is
    staged only when the backup carries a snapshot (``replace_db``), so this
    uses the snapshot-present path."""
    import tortoise.backup as backup_mod

    dest_events, dest_db = _dest(tmp_path)
    dest_events.write_text('{"type":"PointAdded","point":{"id":"dest-p1"}}')
    dest_db.write_bytes(b"ORIGINAL-DB-BYTES")
    src = _backup_dir(tmp_path, "[]\n", snapshot=rdb_snapshot)
    before = dest_events.read_bytes()

    real_replace = backup_mod.os.replace

    def _flaky_replace(source, target):
        # Only the DB's stage fails, and only after the journal's stage has
        # already renamed the journal aside (it runs first).
        if str(source) == str(dest_db) and "restore-aside" in str(target):
            raise PermissionError("injected: the DB stage cannot rename (review)")
        return real_replace(source, target)

    monkeypatch.setattr(backup_mod.os, "replace", _flaky_replace)

    with pytest.raises(PermissionError):
        restore(str(src), db_path=str(dest_db), events_path=str(dest_events),
                into_falkor=True)

    assert dest_events.read_bytes() == before, (
        "the already-staged journal was not restored")
    assert dest_db.read_bytes() == b"ORIGINAL-DB-BYTES", (
        "the destination DB was disturbed by a failed stage")
    assert _asides(tmp_path) == [], "a staging failure left an aside behind"


# ── the AOF dir rule has one home ────────────────────────────────────────


def test_remove_stale_aof_deletes_exactly_what_the_rollback_stages(tmp_path):
    """FAILS IF: `stale_aof_dirs` drifts from what `remove_stale_aof` deletes —
    an AOF name deleted by one surface and missed by the other re-opens the
    #7767 window for that name (the re-derived-rule class of #5285).
    REACHABLE: both tolerated names (#915's per-DB `-appendonlydir` and the
    legacy literal sibling), each created as a dir."""
    db = tmp_path / "tortoise.db"
    per_db = Path(str(db) + "-appendonlydir")
    legacy = tmp_path / "appendonlydir"
    for d in (per_db, legacy):
        d.mkdir()
        (d / "appendonly.aof").write_text("x")

    assert set(stale_aof_dirs(db)) == {per_db, legacy}

    remove_stale_aof(db)
    assert not per_db.exists()
    assert not legacy.exists()
