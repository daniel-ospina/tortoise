"""D2 (#14342) — the spool cannot lie: a retained-unreadable entry is COUNTED,
and no writer path overwrites bytes it could not use.

Three absorbed issues, one invariant:

* **#12483** — a retained unreadable entry (#11802) is invisible to BOTH
  ceilings, because both compute the spool's size from PARSED metas and its turn
  log keeps its live ``.turns.jsonl`` name. Measured before this fix: one filed
  entry plus one unreadable meta with a 5,000-byte turn log held 5,228 bytes in
  ``entries/`` and ``spool_saturated(max_total_bytes=1000)`` returned **False**.
* **#12484** — a non-UTF-8 meta was DESTROYED by this leg while the Pi leg FILED
  it (pinned in ``test_capture_spool.py``).
* **#12487** — ``read_spool_meta`` answers ``None`` for both "absent" and
  "unreadable", so the WRITER built the new entry over ``stored = []``: the log
  was REWRITTEN from the snapshot (destroying stored turns) and ``filed_key`` /
  ``attempts`` / ``next_attempt_at_ms`` were wiped.

All three are the same rule stated at three surfaces: **a refusal to hand over
bytes is not evidence about their content, their absence, or their size.**

Architecture constraints this work obeys (verified, per #14342's research
mandate): ``docs/architecture/STORAGE-ARCHITECTURE.md`` §2.0 (Postgres is the
system of record, FalkorDB a derived cache) is untouched — this is the local
spool, upstream of both, and nothing here writes a captured turn into the graph.
``docs/ONTOLOGY.md``'s episodic-then-semantic order is untouched — the spool
holds raw turns and files them as a session; extraction remains the only producer
of the logical model. ``docs/architecture/EXTRACTOR-V4-ARCHITECTURE.md`` is
untouched — no extraction logic is added to the drain or the spool.
"""
from __future__ import annotations

import json
from pathlib import Path

from tortoise.capture_spool import (
    Bounds,
    Snapshot,
    _entry_is_filed,
    _log_path,
    _meta_path,
    capture_health_line,
    list_spool_metas,
    prune_spool,
    read_discards,
    read_spool_meta,
    spool_saturated,
    write_spool_entry,
)

TURNS = [
    {"role": "user", "content": "Ship the capture spool."},
    {"role": "assistant", "content": "On it."},
]

ROOMY = Bounds(max_entries=1000, max_total_bytes=1 << 30, max_entry_bytes=1 << 20)


def _snapshot(session_id: str = "s1", turns: list[dict] | None = None, **kw) -> Snapshot:
    return Snapshot(
        session_id=session_id,
        turns=turns if turns is not None else list(TURNS),
        source="t.claude.jsonl",
        machine_id="m" * 64,
        harness="claude",
        **kw,
    )


def _entries(root: Path) -> Path:
    return root / "entries"


def _write_meta(root: Path, session_id: str, **fields) -> Path:
    """A meta at the path the spool really uses (`entry_key(session_id)`)."""
    path = _meta_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": 1, "session_id": session_id, **fields}
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def _write_turns(root: Path, session_id: str, turns: list[dict]) -> Path:
    path = _log_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(t) + "\n" for t in turns), encoding="utf-8")
    return path


def _lock(path: Path) -> None:
    path.chmod(0o000)


def _unlock(path: Path) -> None:
    if path.exists():
        path.chmod(0o600)


# ── #12483 — the retained class is COUNTED by both ceilings ─────────────────


def test_a_retained_unreadable_entry_counts_against_the_byte_ceiling(tmp_path):
    """The measured defect, exactly: 5,228 bytes on disk, ceiling 1,000 bytes,
    and the predicate answering False.

    The unreadable entry is in NEITHER ``len(metas)`` nor the byte sum, and its
    turn log still has its LIVE name, so ``_orphan_paths`` does not see it
    either — nothing counted it, and writes kept being admitted past the ceiling
    with no refusal and no ``capture: SATURATED``.

    MUTATION THAT REDS THIS: drop ``unreadable_bytes`` from
    ``_saturated_for_metas``'s total (the pre-fix arithmetic) -> False.
    """
    _write_meta(tmp_path, "sess-filed", capture_key="K", filed_key="K",
                capture_lane="hook", filed_lane="hook")
    _write_turns(tmp_path, "sess-filed", TURNS[:1])
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked",
                 [{"role": "user", "content": "y" * 5000}])
    _lock(locked)
    try:
        on_disk = sum(f.stat().st_size for f in _entries(tmp_path).iterdir())
        assert on_disk > 5000, "the premise: the unreadable entry really is on disk"
        bounds = Bounds(max_entries=1000, max_total_bytes=1000,
                        max_entry_bytes=1 << 20)
        assert spool_saturated(tmp_path, bounds) is True, (
            "the byte ceiling must ENFORCE against a retained-unreadable entry")
    finally:
        _unlock(locked)


def test_the_health_line_reaches_the_same_saturated_verdict(tmp_path):
    """The other ceiling READER: ``capture_health_line`` computes SATURATED from
    the one read-only scan. If it did not carry the unreadable bytes, a box whose
    spool cannot grow would print a healthy line — the false PASS agent-infra#2100
    exists to remove.

    MUTATION THAT REDS THIS: drop the third element from ``_health_scan`` (or the
    ``unreadable_bytes=`` pass-through) -> the line stops saying SATURATED.
    """
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked", [{"role": "user", "content": "y" * 5000}])
    _lock(locked)
    try:
        health = capture_health_line(
            tmp_path,
            bounds=Bounds(max_entries=1000, max_total_bytes=1000,
                          max_entry_bytes=1 << 20))
        assert health.state == "saturated", health.line
    finally:
        _unlock(locked)


def test_prune_does_not_spend_a_filed_entry_for_room_it_cannot_make(tmp_path):
    """A filed entry is the CERTAIN zero-cost class, so it is evicted first when
    that makes room. Here it cannot: the retained-unreadable bytes alone exceed
    the ceiling, so evicting the filed entry leaves the spool over it — a
    destruction that buys nothing, one write at a time.

    MUTATION THAT REDS THIS: remove the ``bytes_reachable`` floor check in
    ``prune_spool`` -> the filed entry is unlinked (its meta disappears).
    """
    filed_meta = _write_meta(tmp_path, "sess-filed", capture_key="K", filed_key="K",
                             capture_lane="hook", filed_lane="hook")
    _write_turns(tmp_path, "sess-filed", TURNS[:1])
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked", [{"role": "user", "content": "y" * 5000}])
    _lock(locked)
    try:
        bounds = Bounds(max_entries=1000, max_total_bytes=1000,
                        max_entry_bytes=1 << 20)
        assert prune_spool(tmp_path, None, bounds) == []
        assert filed_meta.exists(), "an eviction that cannot reach the ceiling is not an eviction"
        assert _meta_path(tmp_path, "sess-filed").exists()
    finally:
        _unlock(locked)


def test_a_write_is_refused_once_the_retained_bytes_are_all_that_is_left(tmp_path):
    """The end-to-end consequence, through the WRITE path: once nothing evictable
    remains, a new capture is REFUSED (``spool_saturated``), recorded, and the
    unreadable entry keeps its bytes. Before #12483 the write was admitted and
    the directory grew with no refusal and no record.

    MUTATION THAT REDS THIS: the pre-fix byte arithmetic -> ``written`` is True.
    """
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked", [{"role": "user", "content": "y" * 5000}])
    _lock(locked)
    try:
        bounds = Bounds(max_entries=1000, max_total_bytes=1000,
                        max_entry_bytes=1 << 20)
        res = write_spool_entry(tmp_path, _snapshot("sess-new"), bounds)
        assert res["written"] is False
        assert res["discards"][-1]["reason"] == "spool_saturated"
        assert locked.exists() and _log_path(tmp_path, "sess-locked").exists()
    finally:
        _unlock(locked)


def test_an_ordinary_spool_is_unaffected_by_the_new_term(tmp_path):
    """Guard the other direction: the counted term is zero when nothing is
    unreadable, so a healthy spool's verdicts are unchanged."""
    write_spool_entry(tmp_path, _snapshot("sess-a"), ROOMY)
    assert spool_saturated(
        tmp_path, Bounds(max_entries=10, max_total_bytes=1 << 20,
                         max_entry_bytes=1 << 20)) is False
    assert capture_health_line(
        tmp_path, bounds=Bounds(max_entries=10, max_total_bytes=1 << 20,
                                max_entry_bytes=1 << 20)).state == "draining"


# ── #12487 — the writer never overwrites what it could not use as a prior ───


def test_the_writer_quarantines_a_diverged_snapshot_instead_of_overwriting(tmp_path):
    """Measured before the fix: with an unreadable meta beside a six-turn log,
    one snapshot carrying a single turn returned ``written: True``, the log fell
    from 6 lines to 1, and the filing stamp was wiped.

    The stored turns are now QUARANTINED (bytes preserved, still counted by the
    byte ceiling) and the entry is resynced from the snapshot — never
    overwritten.

    MUTATION THAT REDS THIS: replace ``prior = _read_prior_meta(...)`` with
    ``prior = read_spool_meta(...)`` (the pre-fix read) -> the log is rewritten
    and the 5 extra turns are gone.
    """
    locked = _write_meta(tmp_path, "sess-w", filed_key="OLDKEY", capture_key="OLDKEY",
                         capture_lane="hook", filed_lane="hook")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    _write_turns(tmp_path, "sess-w", stored)
    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-w", turns=stored[:1]), ROOMY)
        assert res["written"] is True, "the entry must be able to RESYNC"
        assert res["discards"][-1]["reason"] == "meta_unreadable"
        assert res["discards"][-1]["retained"] is True
        assert "QUARANTINED" in res["discards"][-1]["detail"]
    finally:
        _unlock(locked)

    orphans = sorted(_entries(tmp_path).glob("*.turns.jsonl.orphan*"))
    assert len(orphans) == 1, orphans
    assert len(orphans[0].read_text(encoding="utf-8").splitlines()) == 6, \
        "every stored turn survives — on disk, even though it left the queue"
    # …and the entry is REPAIRED: the meta is readable again and carries this write.
    repaired = read_spool_meta(tmp_path, "sess-w")
    assert repaired is not None and repaired["turns_count"] == 1
    assert _entry_is_filed(repaired) is False, (
        "the filing state genuinely cannot be carried forward — it lived in the"
        " bytes that could not be read; the record says so")


def test_the_writer_appends_to_a_live_session_it_could_not_read(tmp_path):
    """A live session grows every turn. When the snapshot provably EXTENDS the
    stored log, an append destroys nothing, so it is admitted — refusing here
    would turn one corrupt byte into an ongoing silent loss (the session would
    never spool again). The meta is repaired on the way through.

    MUTATION THAT REDS THIS: refuse every unusable-prior write (the naive
    reading of #12487) -> ``written`` is False and the new turn is lost.
    """
    locked = _write_meta(tmp_path, "sess-w", filed_key="K", capture_key="K")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    log = _write_turns(tmp_path, "sess-w", stored)
    _lock(locked)
    try:
        grown = [*stored, {"role": "assistant", "content": "new"}]
        res = write_spool_entry(tmp_path, _snapshot("sess-w", turns=grown), ROOMY)
        assert res["written"] is True
        assert res["discards"][-1]["retained"] is True
        assert "APPENDED" in res["discards"][-1]["detail"]
    finally:
        _unlock(locked)
    assert len(log.read_text(encoding="utf-8").splitlines()) == 7
    assert read_spool_meta(tmp_path, "sess-w") is not None, "the entry is repaired"


def test_the_writer_refuses_when_it_cannot_read_the_LOG_either(tmp_path):
    """Nothing about the entry is knowable, and a resync would MOVE a
    conversation no surface could then deliver. Refuse; leave it byte-identical.
    This is #12487's own case (an entry the process may not read).

    MUTATION THAT REDS THIS: drop the ``TURNS_UNREADABLE`` arm -> the writer
    quarantines (moves) a log it never read.
    """
    locked = _write_meta(tmp_path, "sess-w")
    log = _write_turns(tmp_path, "sess-w", TURNS)
    _lock(locked)
    _lock(log)
    try:
        grown = [*TURNS, {"role": "assistant", "content": "new"}]
        res = write_spool_entry(tmp_path, _snapshot("sess-w", turns=grown), ROOMY)
        assert res["written"] is False
        assert res["discards"][-1]["reason"] == "meta_unreadable"
        assert res["discards"][-1]["retained"] is True
        assert "REFUSED" in res["discards"][-1]["detail"]
    finally:
        _unlock(locked)
        _unlock(log)
    assert not list(_entries(tmp_path).glob("*.orphan*")), \
        "a log this process could not read must never be moved aside"
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(TURNS)


def test_a_corrupt_json_meta_is_unusable_for_the_writer_but_still_repaired(tmp_path):
    """The symmetric arm: bytes that WERE read and are not a meta. The writer
    treats them as unusable (it cannot carry the filing state), but it does not
    destroy them — the resync quarantines the log and writes a fresh entry, and
    the next repairing scan sees a healthy spool.

    MUTATION THAT REDS THIS: fall through to the pre-fix path -> the stored
    turns are silently gone with no record.
    """
    meta = _meta_path(tmp_path, "sess-c")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text("{not json", encoding="utf-8")
    _write_turns(tmp_path, "sess-c", TURNS)
    stored = list(TURNS)
    res = write_spool_entry(tmp_path, _snapshot("sess-c", turns=stored[:1]), ROOMY)
    assert res["written"] is True
    assert res["discards"][-1]["reason"] == "meta_unreadable"
    orphans = list(_entries(tmp_path).glob("*.turns.jsonl.orphan*"))
    assert orphans and len(orphans[0].read_text(encoding="utf-8").splitlines()) == 2
    assert read_spool_meta(tmp_path, "sess-c") is not None


def test_the_drain_still_reports_a_corrupt_meta_as_corrupt(tmp_path):
    """The other direction, pinned so the #12484 arm does not swallow the #7708
    path: bytes that were READ and are not usable JSON still record
    ``corrupt_entry``, still quarantine the turn log, and still remove the meta.

    MUTATION THAT REDS THIS: send every non-meta outcome to the unreadable arm.
    """
    entries = _entries(tmp_path)
    entries.mkdir(parents=True)
    (entries / "bad.meta.json").write_text("{not json", encoding="utf-8")
    (entries / "bad.turns.jsonl").write_text("{}\n", encoding="utf-8")
    metas, discards = list_spool_metas(tmp_path)
    assert metas == []
    assert [d["reason"] for d in discards] == ["corrupt_entry"]
    assert (entries / "bad.turns.jsonl.orphan").read_text(encoding="utf-8") == "{}\n"
    assert not (entries / "bad.meta.json").exists()


def test_the_two_legs_no_longer_disagree_DESTRUCTIVELY_on_non_utf8(tmp_path):
    """#12484's actual claim: whichever leg touches the bytes first, nothing is
    destroyed. Python retains; the Pi leg files. The remaining asymmetry is
    stated in the PR (retention is the only disposition that cannot lose the
    bytes from this seat), but the DESTRUCTION is gone.

    MUTATION THAT REDS THIS: put non-UTF-8 back on the corrupt path -> the meta
    and its log are gone.
    """
    meta = _meta_path(tmp_path, "sess-latin")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_bytes(b'{"session_id":"caf\xe9"}')
    log = _write_turns(tmp_path, "sess-latin", TURNS)
    metas, discards = list_spool_metas(tmp_path)
    assert metas == []
    assert [d["reason"] for d in discards] == ["meta_unreadable"]
    assert discards[0]["retained"] is True
    assert meta.exists() and log.exists()
    assert not list(_entries(tmp_path).glob("*.orphan*"))
    assert [d["reason"] for d in read_discards(tmp_path)] == ["meta_unreadable"]
