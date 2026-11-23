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

import pytest

from tortoise.capture_spool import (
    Bounds,
    Snapshot,
    _cheap_spool_scan,
    _entry_bytes,
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
    """Make ``path`` unreadable — and REFUSE to grade a tautology.

    ``chmod(0o000)`` denies a root user nothing, so on a root runner every row
    built on this helper would grade a READABLE file while claiming to exercise a
    read REFUSAL (#14347 review, non-blocking note 2). The premise is asserted at
    the point it is established instead of assumed: if the read still succeeds,
    undo the lock and SKIP with the reason, so an unsatisfiable premise reads as
    a skip and never as a pass.
    """
    path.chmod(0o000)
    try:
        path.read_bytes()
    except OSError:
        return
    path.chmod(0o600)
    pytest.skip(
        "chmod(0o000) does not deny this runner's user (root): the 'cannot be"
        " read' premise these rows rest on cannot be constructed here, so the"
        " row would grade a READABLE file")


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


def test_the_writer_refuses_when_ONLY_the_LOG_cannot_be_read(tmp_path):
    """(cycle-3 F1) The LOG axis is an axis, not a sub-case of the META one.

    A READABLE meta beside an UNREADABLE turn log used to skip the
    ``TURNS_UNREADABLE`` guard, because the only reader of ``stored_state`` sat
    inside the ``if unusable:`` (meta) arm. ``stored == []`` then made
    ``extends`` False and the write reached ``_atomic_write(log_path, ...)`` —
    and ``os.replace`` needs only DIRECTORY write permission, so a mode-``000``
    log was REPLACED by the snapshot with ``written: True`` and no record at
    all. Measured on the pre-fix head: 3 lines -> 1, ``discards == []``, no
    ``.orphan``. #11802 (recorded) rules that a read refusal must never
    destroy, so the refusal is hoisted out of the meta arm and pinned here.

    The sibling test above locks the meta AND the log, so it passes through the
    ``unusable`` arm and never reaches this case — which is exactly why the
    defect survived two cycles.

    MUTATION THAT REDS THIS: re-nest the guard inside the ``unusable`` arm (or
    drop it) -> ``written`` is True, the log falls from 3 lines to 1, and the
    ledger is empty.
    """
    meta = _write_meta(tmp_path, "sess-w", content_digest="digest-of-six-turns")
    log = _write_turns(tmp_path, "sess-w", TURNS)
    meta_before = meta.read_bytes()
    _lock(log)
    try:
        shorter = [{"role": "assistant", "content": "only this one"}]
        res = write_spool_entry(tmp_path, _snapshot("sess-w", turns=shorter), ROOMY)
        assert res["written"] is False, "a log it could not read is never overwritten"
        assert res["discards"], "a refusal must be recorded, not silent"
        assert res["discards"][-1]["reason"] == "meta_unreadable"
        assert res["discards"][-1]["retained"] is True
        assert "REFUSED" in res["discards"][-1]["detail"]
        assert "turn log could not be read" in res["discards"][-1]["detail"]
    finally:
        _unlock(log)
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(TURNS), \
        "the stored turns are byte-identical after the refusal"
    assert meta.read_bytes() == meta_before, "the readable meta is not rewritten either"
    assert not list(_entries(tmp_path).glob("*.orphan*")), \
        "a log this process could not read must never be moved aside"


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

    ⚠️ WHAT THIS EXERCISES: the PYTHON leg only. The Pi leg's half of the pair —
    including its `unreadableBytes` term in `spoolSaturated` — is pinned in
    `tortoise/pi-hooks/tortoise-capture.test.ts` (#14347 review F5), so the two
    legs' claims are covered on the leg that owns each of them.

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


# ── #14347 review — the seams the new third state opens ────────────────────
#
# The three-state split (`_read_prior_meta`) is right; these pin the places where
# the NEW state met arithmetic and control flow that still assumed the old two.
# Each test names the mutation that reopens its defect.


def test_a_growth_of_an_unreadable_entry_is_counted_ONCE(tmp_path):
    """#14347 review F2 — ``prior is None`` is not "nothing is on disk".

    In the UNUSABLE arm the meta cannot be read, but the entry still occupies
    bytes. Asking the admission guard with ``replaces_session_id=None`` made the
    byte total count it TWICE — once through the retained-unreadable term, once
    through a full-entry projection that was never subtracted — so a growth whose
    true post-write size is under the ceiling was REFUSED, at roughly half the
    ceiling, forever (the refusal replays from its stamp). It also made the
    health line disagree with the writer: the health line does not project, so it
    reported the same spool healthy.

    MUTATION THAT REDS THIS: ``replaces_session_id=(snapshot.session_id if prior
    else None)`` — the pre-fix line. `written` becomes False and the health line
    calls the same spool fine.
    """
    locked = _write_meta(tmp_path, "sess-g", filed_key="OLDKEY")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    _write_turns(tmp_path, "sess-g", stored)
    grown = [*stored, {"role": "assistant", "content": "turn-6"}]

    # The size the write will really put on disk, measured on a twin spool so the
    # bound is derived from the code's own definition rather than hardcoded.
    probe = tmp_path / "probe"
    write_spool_entry(probe, _snapshot("sess-g", turns=grown), ROOMY)
    projected = _entry_bytes(probe, "sess-g")
    # Room for the post-write entry and nothing else. Double-counting the entry
    # it replaces overflows this by its own size.
    bounds = Bounds(max_entries=1000, max_total_bytes=projected + 8,
                    max_entry_bytes=1 << 20)

    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-g", turns=grown), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is True, (
        "the entry this write REPLACES must be subtracted from the projection — "
        f"counted twice it overflows a ceiling the write cannot cross: {res['discards']}")
    assert not [d for d in res["discards"] if d["reason"] == "spool_saturated"]
    assert spool_saturated(tmp_path, bounds) is False, (
        "the writer admitted the write, so the same spool must not read SATURATED")
    assert capture_health_line(tmp_path, bounds=bounds).state != "saturated", (
        "a healthy health line over a refused write is the false PASS (#2100) "
        "this accounting exists to remove")


def test_a_REFUSED_write_does_not_quarantine_the_log_it_refuses_to_replace(tmp_path):
    """#14347 review F3 — a refusal must not mutate the spool.

    On the diverging disposition the writer quarantined the stored log (a rename
    to ``.orphan``) BEFORE the guards decided, so a REFUSED write had already
    moved the only addressable copy of the transcript its own refusal text
    promised was "kept", on exactly the saturated box that makes the refusal
    likely. Nothing may move until the write is admitted.

    MUTATION THAT REDS THIS: call ``_quarantine_log(log_path)`` inside the
    ``unusable`` block, above the guards — the log leaves its live name and an
    ``.orphan`` appears for a write that was never written.
    """
    locked = _write_meta(tmp_path, "sess-r")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    log = _write_turns(tmp_path, "sess-r", stored)
    _lock(locked)
    try:
        bounds = Bounds(max_entries=1000, max_total_bytes=1,
                        max_entry_bytes=1 << 20)
        res = write_spool_entry(tmp_path, _snapshot("sess-r", turns=stored[:1]), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is False
    assert res["discards"][-1]["reason"] == "spool_saturated"
    assert not list(_entries(tmp_path).glob("*.orphan*")), (
        "a refused write must leave the entry byte-identical: the transcript was "
        "moved aside for a write that never happened")
    assert log.exists(), "the stored log must still be at its live, addressable name"
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(stored)


def test_a_FAILED_quarantine_refuses_instead_of_overwriting_the_log(tmp_path, monkeypatch):
    """#14347 review F4 — `_quarantine_log` returns ``None`` for TWO outcomes.

    "Nothing to keep" and "the rename FAILED" were indistinguishable to the
    caller, so a failed quarantine fell through to ``_atomic_write`` over the
    still-present stored log: the turns were DESTROYED while the record said
    ``retained: True`` (a destruction disclosed as a retention — fail-open). A
    log that still has bytes after a failed quarantine is the third disposition:
    REFUSE, entry byte-identical.

    MUTATION THAT REDS THIS: accept ``None`` from ``_quarantine_log`` without
    checking that the log is gone — `written` becomes True and the six stored
    turns are replaced by one.
    """
    import tortoise.capture_spool as spool_mod

    locked = _write_meta(tmp_path, "sess-q")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    log = _write_turns(tmp_path, "sess-q", stored)
    real_replace = spool_mod.os.replace

    def refuse_orphan(src, dst, *a, **kw):
        # Only the quarantine move fails; the tmp -> live-log replace still works,
        # which is the exact shape that turned the failure into an overwrite.
        if str(dst).find(".orphan") >= 0:
            raise OSError(13, "Permission denied", str(dst))
        return real_replace(src, dst, *a, **kw)

    monkeypatch.setattr(spool_mod.os, "replace", refuse_orphan)
    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-q", turns=stored[:1]), ROOMY)
    finally:
        _unlock(locked)
        monkeypatch.setattr(spool_mod.os, "replace", real_replace)

    assert res["written"] is False
    assert res["discards"][-1]["retained"] is True
    assert "could not be moved aside" in res["discards"][-1]["detail"]
    assert not list(_entries(tmp_path).glob("*.orphan*"))
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(stored), (
        "a quarantine that failed must never be followed by an overwrite of the "
        "very log it could not move")


# ── cycle 2 — the seams the THIRD state opens ───────────────────────────────
#
# The three-state prior read (`PRIOR_META_OK` / `ABSENT` / `UNUSABLE`) is right;
# these pin the places where the NEW state still met arithmetic and control flow
# written for the old two. Each one is a distinct defect with its own mutation.


def test_a_byte_bearing_log_that_parses_to_ZERO_turns_is_quarantined_not_overwritten(tmp_path):
    """The gate on the diverging disposition is BYTES, not PARSED TURNS.

    `stored` is the parsed turn list, so a log that has bytes but yields ZERO
    turns — a torn first append, a foreign/partial format — made
    ``divergent_turns == 0``, skipped the quarantine entirely and was
    OVERWRITTEN by the full-snapshot write: ``written: True``, no discard record,
    no ``.orphan``. That is #12487's own signature, reintroduced by gating a BYTE
    decision on a PARSE result.

    MUTATION THAT REDS THIS: gate the quarantine on ``divergent_turns`` instead of
    ``divergent_bytes`` -> `written` is True with no record and the 22 stored
    bytes are replaced.
    """
    locked = _write_meta(tmp_path, "sess-z")
    # JSON lines, but no line is a TURN (no usable role/content) — the log is
    # byte-bearing and parses to nothing.
    log = _log_path(tmp_path, "sess-z")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"note": "half-written"}\n[1, 2, 3]\n', encoding="utf-8")
    original = log.read_bytes()
    assert original, "the premise: the stored log really has bytes"
    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-z"), ROOMY)
    finally:
        _unlock(locked)

    orphans = list(_entries(tmp_path).glob("*.orphan*"))
    assert res["written"] is True
    assert orphans, "the stored bytes must be PRESERVED, not overwritten"
    assert orphans[0].read_bytes() == original, (
        "the quarantined orphan must hold exactly the bytes that were on disk")
    detail = res["discards"][-1]["detail"]
    assert "QUARANTINED" in detail
    assert "(none)" not in detail, "the record must name the file it preserved"
    assert len(log.read_text(encoding="utf-8").splitlines()) == len(TURNS)


def test_a_REFUSED_write_mutates_NOTHING_even_when_the_prior_is_unreadable(tmp_path):
    """The refusal contract: "a refused write therefore repairs nothing itself".

    The admission probe is itself a scan, and that scan used to repair — so a
    write REFUSED with ``spool_saturated`` had already had its meta unlinked and
    its turn log renamed to ``.orphan``, which the next ``prune_spool`` evicts.
    The refusal text said "the turns the spool already holds are kept" about
    turns the probe had just moved.

    MUTATION THAT REDS THIS: let the admission probe repair (``_scan_spool(...,
    repair=True)`` from ``_admission_saturated``) -> the corrupt entry's meta is
    unlinked and its log renamed to ``.orphan``, on a write that was rejected.

    ⛔ The spool carries a CORRUPT meta on purpose. An earlier version of this
    test used only a ``chmod(0o000)``-unreadable entry, and the repairing scan
    RETAINS that class — so the mutation measured GREEN and the pin could not
    discriminate (cycle-4 P2). Corruption is the only class the repairing path
    mutates: ``_scan_spool`` unlinks the meta and quarantines the log.
    """
    # A full spool of undelivered entries, so admission is refused.
    for sid in ("sess-a", "sess-b"):
        _write_meta(tmp_path, sid, capture_lane="hook")
        _write_turns(tmp_path, sid, TURNS)
    corrupt = _meta_path(tmp_path, "sess-corrupt")
    corrupt.parent.mkdir(parents=True, exist_ok=True)
    corrupt.write_text("{ this is not json", encoding="utf-8")
    _write_turns(tmp_path, "sess-corrupt", TURNS)
    locked = _write_meta(tmp_path, "sess-r")
    log = _write_turns(tmp_path, "sess-r",
                       [{"role": "user", "content": f"t{i}"} for i in range(6)])
    before = log.read_bytes()
    _lock(locked)
    try:
        res = write_spool_entry(
            tmp_path, _snapshot("sess-r"), Bounds(max_entries=2))
    finally:
        _unlock(locked)

    assert res["written"] is False
    assert log.exists(), "a REFUSED write must leave the turn log in place"
    assert log.read_bytes() == before, "…and byte-identical"
    assert not list(_entries(tmp_path).glob("*.orphan*")), (
        "a REFUSED write is not a mutation: nothing may be moved aside")
    assert corrupt.exists(), (
        "and the corrupt meta is still there — repairing it was not this call's job")
    assert read_spool_meta(tmp_path, "sess-a") is not None
    assert read_spool_meta(tmp_path, "sess-b") is not None


def test_the_COUNT_ceiling_counts_retained_unreadable_entries(tmp_path):
    """#12483 says the entry was invisible to **both** ceilings — the count one
    was still blind after the byte axis was fixed.

    ``_saturated_for_metas`` counts ``len(metas)``, and an unreadable meta never
    reaches that list, so six unreadable entries with ``max_entries=3`` answered
    False and writes were admitted past the count ceiling without limit.

    MUTATION THAT REDS THIS: drop ``unreadable_count`` from ``parsed_count`` ->
    False with six entries on disk and a ceiling of three.
    """
    locked = []
    for i in range(6):
        sid = f"sess-u{i}"
        locked.append(_write_meta(tmp_path, sid))
        _write_turns(tmp_path, sid, TURNS)
    try:
        for path in locked:
            _lock(path)
        bounds = Bounds(max_entries=3)
        assert spool_saturated(tmp_path, bounds) is True
        assert list_spool_metas(tmp_path)[0] == [], "the premise: none of them parse"
    finally:
        for path in locked:
            _unlock(path)


def test_the_writer_and_the_health_line_AGREE_at_the_count_ceiling(tmp_path):
    """The count answer is the POST-WRITE entry count, in both directions.

    At ``max_entries=2`` with one readable and one ``chmod(0o000)``-unreadable
    entry, ``parsed_count`` is already 2 — so a GROWTH of the unreadable entry
    adds no entry and must be ADMITTED. It was refused, permanently, by an
    ``adds_entry=(prior is None)`` operand written when ``parsed_count`` was
    ``len(metas)`` alone: the count term started carrying the unreadable class
    in the same commit, so the same entry was counted by both operands and the
    write path over-counted by one. Both readers must answer about one
    directory.

    Control (measured): the same spool and the same snapshot, only the meta's
    readability differing — unreadable ⇒ ``written: False
    ['spool_saturated']``; readable ⇒ ``written: True
    ['spool_count_exceeded']``. The asymmetry IS the defect.

    MUTATION THAT REDS THIS: pass ``adds_entry=(prior is None)`` ->
    ``parsed_count + 1 = 3 > max_entries = 2`` -> the growth is REFUSED while
    the health line calls the spool relievable.

    ⚠️ The health line is deliberately NOT used as the oracle here: with no
    write in hand it has no projection, so it takes the STRICT arm
    (``parsed_count >= max_entries``) and answers "would a NEW capture be
    refused?" — a different question from "does THIS replacement cross the
    ceiling?". The two are not a disagreement; the writer answers the one it was
    asked.
    """
    _write_meta(tmp_path, "sess-ok", capture_lane="hook")
    _write_turns(tmp_path, "sess-ok", TURNS)
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked", TURNS)
    bounds = Bounds(max_entries=2)
    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-locked"), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is True, (
        "a replacement at the ceiling crosses nothing and must be admitted")


def test_a_replacement_OVER_the_count_ceiling_is_refused(tmp_path):
    """…and the same write one entry over is refused, so the agreement above is
    not an unconditional admit.

    MUTATION THAT REDS THIS: pass ``adds_entry=False`` unconditionally (or drop
    the count term entirely) -> the write is ADMITTED at
    ``parsed_count = 3 > max_entries = 2``.
    """
    for sid in ("sess-a", "sess-b"):
        _write_meta(tmp_path, sid, capture_lane="hook")
        _write_turns(tmp_path, sid, TURNS)
    locked = _write_meta(tmp_path, "sess-locked")
    _write_turns(tmp_path, "sess-locked", TURNS)
    bounds = Bounds(max_entries=2)
    _lock(locked)
    try:
        res = write_spool_entry(tmp_path, _snapshot("sess-locked"), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is False, (
        "with no filed entry to evict, a replacement over the ceiling is refused")
    assert res["discards"][-1]["reason"] == "spool_saturated"


def test_prune_spends_a_filed_entry_for_a_count_overage_it_can_see(tmp_path):
    """``prune_spool``'s count term must name the population the CEILING was
    measured against.

    ``_saturated_for_metas`` counts ``len(metas) + unreadable_count``, but the
    eviction loop counted ``len(metas)`` alone, so with K retained-unreadable
    entries the spool settled at ``max_entries + K``: every admitted write spent
    one filed entry and the predicate's own ceiling was never reached. Measured
    pre-fix (3 filed + 1 ``chmod(0o000)``, ``max_entries=3``): ``prune_spool →
    []``, and the write admitted with ``spool_count_exceeded`` while the
    post-write count was STILL 4.

    ``spool_saturated`` is correctly False here — it answers "nothing
    EVICTABLE", and there are three filed entries — so prune is the only surface
    that can relieve it, which is exactly why its count term has to see the
    class.

    MUTATION THAT REDS THIS: use ``len(metas)`` for ``count`` -> prune returns
    ``[]`` and nothing is reclaimed.
    """
    for i in range(3):
        _filed_entry(tmp_path, f"sess-f{i}", f"2026-01-01T00:00:0{i}Z")
    locked = _write_meta(tmp_path, "sess-u")
    _write_turns(tmp_path, "sess-u", TURNS)
    bounds = Bounds(max_entries=3)
    _lock(locked)
    try:
        assert spool_saturated(tmp_path, bounds) is False, (
            "the premise: 3 filed entries ARE evictable, so this is not"
            " 'saturated', it is prune's job")
        discards = prune_spool(tmp_path, keep_session_id="sess-new", bounds=bounds)
    finally:
        _unlock(locked)

    assert [d["reason"] for d in discards] == ["spool_count_exceeded"], (
        f"one filed entry must be spent to reach the ceiling: {discards}")
    assert locked.exists(), "the unreadable entry is never the thing evicted"


def test_a_resync_that_could_not_KEEP_its_orphan_is_refused_BEFORE_the_rename(tmp_path):
    """The bytes a resync is about to quarantine belong in the projection.

    The quarantine happens after the admission decision, so the bytes that become
    the ``.orphan`` were in neither ``entry_bytes`` nor the replaced entry's
    size — an admitted resync could land the spool over the byte ceiling and the
    ``prune_spool`` on the SAME call then evicted the orphan it had just promised
    to keep (measured: ``QUARANTINED`` followed one line later by ``evicted a
    quarantined log``).

    MUTATION THAT REDS THIS: drop ``pending_orphan_bytes`` from the admission
    call -> the write is admitted and the stored six turns are reaped by the
    prune that follows it.
    """
    locked = _write_meta(tmp_path, "sess-o")
    stored = [{"role": "user", "content": "z" * 100} for _ in range(6)]
    log = _write_turns(tmp_path, "sess-o", stored)
    before = log.read_bytes()
    # The CURRENT total fits; the post-write total cannot, because the orphan
    # those bytes become is not reclaimable at decision time.
    bounds = Bounds(max_entries=1000, max_total_bytes=len(before) + 50,
                    max_entry_bytes=1 << 20)
    _lock(locked)
    try:
        assert spool_saturated(tmp_path, bounds) is False, (
            "the premise: today's bytes alone are under the ceiling")
        res = write_spool_entry(tmp_path, _snapshot("sess-o", turns=TURNS[:1]), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is False
    assert log.exists() and log.read_bytes() == before
    assert not list(_entries(tmp_path).glob("*.orphan*"))


def test_a_corrupt_meta_is_COUNTED_by_a_READ_ONLY_probe(tmp_path):
    """A corrupt meta (read, but unusable) is a class the read-only scan must
    still hand back, or the ceiling it was asked about is unenforced.

    The admission probe must not repair, so it scans with ``repair=False`` — and
    the corrupt arm used to ``continue`` before collecting anything, which would
    have made a stop-repairing fix silently stop COUNTING. ``os.replace`` failing
    is not needed to see it: the entry is 5,000 bytes on a 1,000-byte ceiling.

    MUTATION THAT REDS THIS: collect corrupt stems only under ``repair`` (the
    pre-fix arm) -> False, with 5,000 bytes on disk and nothing counted.
    """
    meta = _meta_path(tmp_path, "sess-corrupt")
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text("{ this is not json", encoding="utf-8")
    _write_turns(tmp_path, "sess-corrupt",
                 [{"role": "user", "content": "y" * 5000}])
    bounds = Bounds(max_entries=1000, max_total_bytes=1000, max_entry_bytes=1 << 20)

    assert spool_saturated(tmp_path, bounds, repair=False) is True
    assert meta.exists(), "a read-only probe must not touch the entry"
    assert list(_entries(tmp_path).glob("*.orphan*")) == []


# ── cycle 3 — the floor's LOOP, the ledger's timing, and the meta-less log ──
#
# Three findings from the review at `9de49e36b801`, each a seam opened by the
# FIX for the previous one: a floor check that the loop below it ignored, a
# disposition record emitted before the decision that admits the write, and an
# occupancy test that re-collapsed "no meta" into "nothing on disk".


def _filed_entry(root: Path, session_id: str, stamp: str) -> Path:
    """A FILED entry (its content is on the wire) with a deterministic age."""
    meta = _write_meta(root, session_id, capture_key="K", filed_key="K",
                       capture_lane="hook", filed_lane="hook",
                       updated_at=stamp, created_at=stamp)
    _write_turns(root, session_id, [{"role": "user", "content": "x"}])
    return meta


def test_prune_stops_at_the_COUNT_ceiling_when_the_byte_ceiling_is_unreachable(tmp_path):
    """Over BOTH ceilings, prune spends only the evictions the COUNT needs.

    ``bytes_reachable`` False means no eviction can bring the byte term under
    ``max_total_bytes`` (the permanent classes alone are over it), so a loop
    still testing ``total <= max_total_bytes`` never stops on that axis: it
    evicted EVERY filed entry to relieve one count slot and was still over the
    byte ceiling afterwards — #10175 ("refuse new work, never destroy the queue
    to admit it") violated from inside the guard added to enforce it.

    MUTATION THAT REDS THIS: restore the loop break
    ``count <= max_entries and total <= max_total_bytes`` -> all five filed
    entries are unlinked and only the undelivered one survives.
    """
    filed = [_filed_entry(tmp_path, f"sess-f{i}", f"2026-01-01T00:00:0{i}Z")
             for i in range(5)]
    _write_meta(tmp_path, "sess-undelivered", capture_key="U")
    _write_turns(tmp_path, "sess-undelivered", [{"role": "user", "content": "y" * 2000}])
    bounds = Bounds(max_entries=3, max_total_bytes=1000, max_entry_bytes=1 << 20)

    discards = prune_spool(tmp_path, None, bounds)

    assert len(discards) == 3, (
        "counting six entries against max_entries=3 needs exactly three"
        f" evictions; prune spent {len(discards)}"
    )
    assert {d["reason"] for d in discards} == {"spool_total_bytes_exceeded"}, (
        "both ceilings are breached, so the record names the byte overage — the"
        " point of the test is HOW MANY entries it spent, not the label")
    assert _meta_path(tmp_path, "sess-undelivered").exists(), (
        "an undelivered entry is never evicted to make room (capture-loss)")
    assert [_meta_path(tmp_path, f"sess-f{i}").exists() for i in range(5)] == \
        [False, False, False, True, True], "oldest-first, and only the count overage"
    assert all(not p.exists() for p in filed[:3])
    surviving = sum(_entry_bytes(tmp_path, sid) for sid in
                    ("sess-f3", "sess-f4", "sess-undelivered"))
    assert surviving > bounds.max_total_bytes, (
        "the byte ceiling was NOT reached by any of those evictions — which is"
        " exactly why spending the filed class for it buys nothing")


def test_a_REFUSED_append_records_NOTHING_about_the_append_it_could_not_do(tmp_path):
    """The ledger describes what the write DID, so a refusal carries no append.

    Measured pre-fix: a saturated spool returned
    ``[<"the write APPENDED 1 new turn(s)">, <spool_saturated refusal>]`` for a
    write that never happened.

    ⛔ The fixture is genuinely OVER the ceiling. With one ``chmod(0o000)`` entry
    alone at ``max_entries=1`` the spool is exactly AT it, and a REPLACEMENT adds
    no entry — so after the cycle-5 finding-1 fix that write is correctly
    ADMITTED and this row could no longer reach the refusal whose ledger it is
    about. The readable entry beside it is what puts the count over.

    MUTATION THAT REDS THIS: emit the append record inside the ``unusable`` arm
    (above the admission guard) -> the detail claims an append the write did not
    perform.
    """
    _write_meta(tmp_path, "sess-other", capture_lane="hook")
    _write_turns(tmp_path, "sess-other", TURNS)
    locked = _write_meta(tmp_path, "sess-w")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    log = _write_turns(tmp_path, "sess-w", stored)
    _lock(locked)
    try:
        bounds = Bounds(max_entries=1, max_total_bytes=1 << 20,
                        max_entry_bytes=1 << 20)
        grown = [*stored, {"role": "assistant", "content": "new"}]
        res = write_spool_entry(tmp_path, _snapshot("sess-w", turns=grown), bounds)
    finally:
        _unlock(locked)

    assert res["written"] is False, (
        "one readable + one unreadable entry at max_entries=1 is over the ceiling")
    assert res["discards"][-1]["reason"] == "spool_saturated"
    details = [d.get("detail", "") for d in res["discards"]]
    assert not any("APPENDED" in d for d in details), (
        f"the ledger must not claim a write that was refused: {details}")
    assert len(log.read_text(encoding="utf-8").splitlines()) == 6, (
        "and the log is byte-identical — which is what makes that claim false")


def test_one_session_RECORDS_BOTH_its_append_and_its_later_quarantine(tmp_path):
    """Two dispositions on one session get two stamps, so neither is suppressed.

    The dedupe key is ``(session_id, reason)`` plus the stamp, and both
    dispositions use ``DISCARD_META_UNREADABLE``: one shared stamp made the
    second a "repeat" of the first. Measured: a session that appended on turn N
    and diverged after a compaction had its QUARANTINE record swallowed and the
    ledger returned the earlier "APPENDED … and overwrote nothing" about turns
    that had just left the live queue for an ``.orphan``.

    MUTATION THAT REDS THIS: give **both** records the same
    ``unusable-meta:<session>`` stamp -> the second is suppressed and the ledger
    holds no QUARANTINED line. ⛔ Moving only the DIVERGE stamp to
    ``unusable-meta:<session>`` measures GREEN (the append still stamps
    ``unusable-meta-append:<session>``, so nothing collides) — an earlier version
    of this docstring named that edit, which is not the collision the test
    describes (cycle-4 P3). The test has the power; the label was wrong.
    """
    locked = _write_meta(tmp_path, "sess-w")
    stored = [{"role": "user", "content": f"turn-{i}"} for i in range(6)]
    _write_turns(tmp_path, "sess-w", stored)
    _lock(locked)
    try:
        grown = [*stored, {"role": "assistant", "content": "new"}]
        first = write_spool_entry(tmp_path, _snapshot("sess-w", turns=grown), ROOMY)
        assert first["written"] is True
        assert "APPENDED" in first["discards"][-1]["detail"]

        # The repaired meta is corrupted again: the SAME live session, still
        # unreadable, now DIVERGING (a compaction moved the window).
        _lock(_meta_path(tmp_path, "sess-w"))
        second = write_spool_entry(
            tmp_path, _snapshot("sess-w", turns=[{"role": "user", "content": "compacted"}]),
            ROOMY)
    finally:
        _unlock(_meta_path(tmp_path, "sess-w"))

    assert second["written"] is True
    assert "QUARANTINED" in second["discards"][-1]["detail"], (
        "the quarantine is a DIFFERENT event from the append and must be recorded"
        f" on its own: {second['discards']}")
    details = [d.get("detail", "") for d in read_discards(tmp_path)]
    assert any("APPENDED" in d for d in details), details
    assert any("QUARANTINED" in d for d in details), details
    orphans = sorted(_entries(tmp_path).glob("*.turns.jsonl.orphan*"))
    assert len(orphans) == 1, orphans
    assert len(orphans[0].read_text(encoding="utf-8").splitlines()) == 7, (
        "the turns that left the live queue are ON DISK — six stored plus the"
        " appended one; the live log was rewritten from the compacted snapshot")


def test_a_META_LESS_turn_log_is_QUARANTINED_not_overwritten(tmp_path):
    """An absent meta is not an empty entry.

    The writer stores the log BEFORE the meta and ``_remove_entry_files`` unlinks
    the meta FIRST, so an ENOSPC or a failed second unlink leaves a byte-bearing
    ``.turns.jsonl`` with no meta. Read as a fresh session, its stored turns were
    REPLACED with no record and no ``.orphan`` — #12487's destroy, one state over.

    MUTATION THAT REDS THIS: ``entry_on_disk = prior_state != PRIOR_META_ABSENT``
    (the pre-fix test) -> ``written: True``, one line in the log, no record, no
    orphan.
    """
    stray = _write_turns(tmp_path, "sess-orphan",
                         [{"role": "user", "content": f"orphan-{i}"} for i in range(3)])
    assert not _meta_path(tmp_path, "sess-orphan").exists()

    res = write_spool_entry(
        tmp_path, _snapshot("sess-orphan", turns=[{"role": "user", "content": "fresh"}]),
        ROOMY)

    assert res["written"] is True
    assert "QUARANTINED" in res["discards"][-1]["detail"]
    assert "ABSENT" in res["discards"][-1]["detail"], (
        "and the record says which state was found, not that a meta it never saw"
        " 'could not be read'")
    orphans = sorted(_entries(tmp_path).glob("*.turns.jsonl.orphan*"))
    assert len(orphans) == 1, orphans
    assert len(orphans[0].read_text(encoding="utf-8").splitlines()) == 3, (
        "every stored turn survives — on disk, even though it left the queue")
    assert len(stray.read_text(encoding="utf-8").splitlines()) == 1, (
        "the live log now holds only this snapshot — the three stored turns left"
        " the queue into the .orphan, they were not deleted")


def test_the_writer_APPENDS_to_a_meta_less_log_it_could_reconcile(tmp_path):
    """The append is PROVABLE here too: the log path comes from the caller's
    session id, so a snapshot that extends the stored turns destroys nothing.

    MUTATION THAT REDS THIS: the pre-fix ``entry_on_disk`` -> the log is
    overwritten (3 lines -> 1) with no record.
    """
    stored = [{"role": "user", "content": f"orphan-{i}"} for i in range(2)]
    log = _write_turns(tmp_path, "sess-orphan", stored)
    grown = [*stored, {"role": "assistant", "content": "new"}]

    res = write_spool_entry(tmp_path, _snapshot("sess-orphan", turns=grown), ROOMY)

    assert res["written"] is True
    assert "APPENDED" in res["discards"][-1]["detail"]
    assert len(log.read_text(encoding="utf-8").splitlines()) == 3
    assert list(_entries(tmp_path).glob("*.orphan*")) == []


def test_a_meta_less_turn_log_is_COUNTED_by_both_ceilings(tmp_path):
    """…and its bytes are not invisible to the ceilings either.

    Nothing counted a lone ``.turns.jsonl``: it is not in ``metas`` (no meta), not
    in ``unreadable`` (built from ``*.meta.json`` names) and not in
    ``_orphan_paths`` (matches only ``.orphan``). Measured pre-fix: 5,000 bytes on
    disk with ``max_total_bytes=1000`` answering not-saturated, and a health line
    reading ``draining``.

    MUTATION THAT REDS THIS: drop the stray-log stems from ``_scan_spool``'s third
    element -> both verdicts flip to not-saturated / draining.
    """
    _write_turns(tmp_path, "sess-orphan", [{"role": "user", "content": "y" * 5000}])

    byte_bounds = Bounds(max_entries=1000, max_total_bytes=1000, max_entry_bytes=1 << 20)
    assert spool_saturated(tmp_path, byte_bounds) is True
    assert capture_health_line(tmp_path, bounds=byte_bounds).state == "saturated"

    count_bounds = Bounds(max_entries=1, max_total_bytes=1 << 20, max_entry_bytes=1 << 20)
    assert spool_saturated(tmp_path, count_bounds) is True, (
        "a lone turn log is an entry the COUNT ceiling cannot see without this")


def test_the_WRITER_and_the_health_line_AGREE_on_a_META_LESS_turn_log(tmp_path):
    """The admission fast path's count term must bound the real predicate's.

    ``_scan_spool`` counts a byte-bearing ``.turns.jsonl`` with no meta, so a
    lone log IS in ``parsed_count``. The fast path ``_cheap_spool_scan`` read its
    count from ``*.meta.json`` NAMES only, so it could not see that class — and
    its own docstring's guarantee ("both numbers here are UPPER BOUNDS … which
    makes 'under both ceilings here' a proof of 'not saturated there'") was
    false for the count term once cycle 3 introduced the class. Measured at the
    cycle-4 head: one meta-less log with ``max_entries=1`` gives
    ``spool_saturated == True`` and health ``saturated``, while
    ``write_spool_entry`` returned ``written: True`` with ``discards: []``.

    That is the same two-reader disagreement cycle-2 finding 4 fixed on the
    BYTE axis, reopened on the COUNT axis by the shape an ENOSPC actually
    produces — the writer stores the log BEFORE the meta.

    MUTATION THAT REDS THIS: drop the ``log_stems - meta_stems`` exit from
    ``_cheap_spool_scan`` (count ``*.meta.json`` names only) -> the cheap exit
    answers "under both ceilings" and the write is ADMITTED while the health
    line says SATURATED.
    """
    _write_turns(tmp_path, "sess-stray", [{"role": "user", "content": "y" * 10}])
    bounds = Bounds(max_entries=1, max_total_bytes=1 << 20, max_entry_bytes=1 << 20)

    assert spool_saturated(tmp_path, bounds) is True, (
        "the premise: the FULL scan counts the lone turn log")
    assert capture_health_line(tmp_path, bounds=bounds).state == "saturated", (
        "the premise: so the health line says so too")

    res = write_spool_entry(tmp_path, _snapshot("sess-fresh"), bounds)

    assert res["written"] is False, (
        "the writer must not admit what the health line calls saturated")
    assert res["discards"][-1]["reason"] == "spool_saturated"


def test_the_cheap_scan_DECLINES_when_a_log_has_no_meta(tmp_path):
    """The fast path's contract is "None means do the full scan", not "guess".

    Pinned directly, because the guarantee above is about the *shape of the
    operand*, not about one writer call: a fast path that answers a number for
    this directory cannot be an upper bound on ``_saturated_for_metas``, whatever
    the caller then does with it.

    MUTATION THAT REDS THIS: return ``(names, total)`` for this directory ->
    ``(0, <bytes>)``, which is an UNDER-count of a spool holding one entry.
    """
    _write_turns(tmp_path, "sess-stray", TURNS)

    assert _cheap_spool_scan(tmp_path) is None, (
        "a log with no meta is an entry this scan cannot count — it must decline")

    # …and it still answers, and answers USEFULLY, for the ordinary shape.
    ordinary = tmp_path / "ordinary"
    ordinary.mkdir()
    _write_meta(ordinary, "sess-ok", capture_lane="hook")
    _write_turns(ordinary, "sess-ok", TURNS)
    assert _cheap_spool_scan(ordinary) == (1, _entry_bytes(ordinary, "sess-ok"))
