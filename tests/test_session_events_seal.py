"""#6388 — the session-events SEAL policy: compress, never discard.

Every row below is a property of the ONE promise this tool makes, which is the
#3870 owner ruling ("kept until the user deletes it") expressed as code: a
record may be *re-encoded*, never *dropped*.  A seal that loses a byte, or that
seals a file a live hook is still appending to, fails these rows.
"""
from __future__ import annotations

import gzip
import contextlib
import errno
import importlib.util
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "seal_session_events.py"


def _load():
    spec = importlib.util.spec_from_file_location("seal_session_events", _TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["seal_session_events"] = module
    spec.loader.exec_module(module)
    return module


seal = _load()

TODAY = date(2026, 10, 30)


def _write(root: Path, day: date, body: bytes = b'{"session_id": "s"}\n',
           *, aged: bool = True) -> Path:
    """Write a day file.  ``aged`` backdates the mtime by a day, i.e. makes it
    SETTLED: the tool refuses to seal a file modified within its grace window,
    so a fixture that looked freshly written would test the wrong branch."""
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{day.isoformat()}.jsonl"
    path.write_bytes(body)
    if aged:
        stamp = time.time() - 86400
        os.utime(path, (stamp, stamp))
    return path


def test_seals_only_days_older_than_the_keep_window(tmp_path):
    """The window is a BOUND, and the boundary is the thing to pin: with
    keep_days=7 a file from exactly 7 days ago stays plain, one from 8 does not.

    Mutation: `day >= cutoff` changed to `day > cutoff` makes the 7-day file
    eligible and REDs the first assertion; `max(1, keep_days)` dropped REDs on
    the keep_days=0 fixture below, where a naive cutoff of `today` would seal
    yesterday's settled file but also make the window meaningless.
    """
    recent = _write(tmp_path, TODAY - timedelta(days=1))
    boundary = _write(tmp_path, TODAY - timedelta(days=7))
    old = _write(tmp_path, TODAY - timedelta(days=8))
    foreign = tmp_path / "notes.jsonl"
    foreign.write_text("not ours\n")

    picked = seal.eligible(tmp_path, keep_days=7, today=TODAY)

    assert old in picked
    assert recent not in picked
    assert boundary not in picked, "the window's own boundary must stay readable"
    assert foreign not in picked, "a name that is not YYYY-MM-DD.jsonl is never ours"


def test_sealed_content_round_trips_byte_identical(tmp_path):
    """The archive IS the evidence: decompressed bytes must equal the original.

    Mutation: `gzip.compress(plain, mtime=0)` replaced by a truncating slice
    (`plain[: len(plain) // 2]`) REDs here — and only here, which is why this
    row exists separately from the verification-failure row below.
    """
    body = b'{"session_id": "a"}\n' * 500
    path = _write(tmp_path, TODAY - timedelta(days=9), body)

    result = seal.seal_one(tmp_path, path)

    assert result.status == "sealed"
    assert result.ok
    assert not path.exists(), "the plain file is removed only after the check"
    archived = seal.archive_path(tmp_path, TODAY - timedelta(days=9))
    assert gzip.decompress(archived.read_bytes()) == body
    assert result.sealed_bytes < result.plain_bytes


def test_plain_file_survives_when_the_sealed_copy_does_not_verify(tmp_path, monkeypatch):
    """THE row for #3870: a failed verification must cost zero records.

    Mutation: delete the `_sha256(gzip.decompress(...)) != digest` guard and
    RED — the source is unlinked over a bad archive.
    """
    path = _write(tmp_path, TODAY - timedelta(days=9), b'{"session_id": "b"}\n')
    before = path.read_bytes()

    real_compress = gzip.compress
    monkeypatch.setattr(
        seal.gzip, "compress",
        lambda data, mtime=0: real_compress(b'{"session_id": "TORN"}', mtime=mtime),
    )
    result = seal.seal_one(tmp_path, path)

    assert result.status == "verify-failed"
    assert not result.ok
    assert path.exists(), "a seal that cannot prove itself must not remove the source"
    assert path.read_bytes() == before
    assert not seal.archive_path(tmp_path, TODAY - timedelta(days=9)).exists()


def test_todays_file_is_never_sealed_even_with_keep_days_zero(tmp_path):
    """`reflect-hook` is appending to today's file WHILE this runs.

    Mutation: drop the `day >= today` guard and RED — a keep_days of 0 then
    makes the live append a seal candidate, which is the torn-record loss this
    tool exists to prevent.
    """
    live = _write(tmp_path, TODAY)

    assert seal.eligible(tmp_path, keep_days=0, today=TODAY) == []
    yesterday = _write(tmp_path, TODAY - timedelta(days=1))
    assert seal.eligible(tmp_path, keep_days=0, today=TODAY) == [yesterday], (
        "keep_days=0 means 'seal everything except today', so a settled file "
        "is eligible while the live append is not")
    # An ABSURD window puts the cutoff in the FUTURE, so the arithmetic alone
    # would reach today's file; the today guard is the only thing excluding it.
    # (Yesterday is legitimately inside a negative window — the point of this
    # fixture is that the LIVE file is not, whatever the window says.)
    absurd = seal.eligible(tmp_path, keep_days=-3, today=TODAY)
    assert absurd == [yesterday]
    assert live not in absurd


def test_check_is_drift_before_and_clean_after(tmp_path, capsys):
    """The alarm half: `--check` is exit 1 while a file is past the window.

    Mutation: `return EXIT_DRIFT` replaced by `return EXIT_OK` REDs the first
    assertion — an alarm that cannot fire is the inert gate this repo forbids.
    """
    _write(tmp_path, TODAY - timedelta(days=30))
    argv = ["--root", str(tmp_path), "--today", TODAY.isoformat(),
            "--keep-days", "7"]

    assert seal.main(argv + ["--check"]) == seal.EXIT_DRIFT
    capsys.readouterr()

    assert seal.main(argv + ["--apply"]) == seal.EXIT_OK
    capsys.readouterr()
    assert seal.main(argv + ["--check"]) == seal.EXIT_OK
    assert "ok —" in capsys.readouterr().out


def test_second_run_is_a_no_op_and_an_interrupted_run_finishes(tmp_path):
    """Idempotence, both shapes: already-sealed, and archive-written-but-source-
    left-behind (the run killed between `os.replace` and `unlink`).

    Mutation: drop the identical-content branch and the second run reports
    `conflict` (RED on the status) instead of finishing the interrupted run.
    """
    day = TODAY - timedelta(days=12)
    path = _write(tmp_path, day, b'{"session_id": "c"}\n')
    first = seal.seal_one(tmp_path, path)
    assert first.status == "sealed"

    # Recreate the interrupted state: archive present, plain source reappears.
    _write(tmp_path, day, b'{"session_id": "c"}\n')
    second = seal.seal_one(tmp_path, path)
    assert second.status == "already-sealed"
    assert second.ok
    assert not path.exists()

    # Nothing plain is left, so a third invocation has no candidate at all.
    assert seal.eligible(tmp_path, keep_days=7, today=TODAY) == []


def test_apply_exits_error_when_a_day_could_not_be_sealed(tmp_path, capsys):
    """A run that seals NOTHING still must not report success.

    Mutation: `return EXIT_ERROR` in main's `bad` branch -> `return EXIT_OK`
    leaves every other row green (measured by the fresh-context reviewer), so
    this row is the only thing standing between a refusal and a false PASS.
    """
    day = TODAY - timedelta(days=11)
    _write(tmp_path, day, b'{"session_id": "new"}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"session_id": "old"}\n', mtime=0))

    code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--apply"])

    assert code == seal.EXIT_ERROR
    assert (tmp_path / f"{day.isoformat()}.jsonl").exists()
    capsys.readouterr()


def test_main_apply_never_seals_todays_file(tmp_path, capsys):
    """The today guard, through the path an operator actually runs.

    Mutation: `pending = eligible(...)` -> `pending = sorted(root.glob(...))`
    in main leaves every other row green, so this row pins the WIRING rather
    than the predicate.
    """
    today_file = _write(tmp_path, TODAY, b'{"session_id": "live"}\n')
    old = _write(tmp_path, TODAY - timedelta(days=20))

    assert seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--apply"]) == seal.EXIT_OK

    assert today_file.exists(), "the live append must survive an --apply"
    assert not seal.archive_path(tmp_path, TODAY).exists()
    assert not old.exists()
    capsys.readouterr()


def test_check_is_an_error_where_it_cannot_see(tmp_path, capsys):
    """An alarm that answers `ok` on a substrate it could not inspect is inert.

    Mutation: the `not root.is_dir()` arm returning EXIT_OK (its pre-review
    value) REDs here; so does dropping the unreadable-archive scan.
    """
    missing = tmp_path / "nope"

    assert seal.main(["--root", str(missing), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--check"]) == seal.EXIT_ERROR
    capsys.readouterr()

    # A truncated archive is evidence that no longer decrypts: drift, not ok.
    day = TODAY - timedelta(days=9)
    seal.seal_one(tmp_path, _write(tmp_path, day, b'{"session_id": "e"}\n'))
    seal.archive_path(tmp_path, day).write_bytes(b"\x1f\x8b\x08 truncated")
    assert seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--check"]) == seal.EXIT_DRIFT
    capsys.readouterr()


def test_a_live_append_is_never_sealed_even_when_its_name_is_old(tmp_path):
    """The producer names its file from UTC; this tool reads the clock.  The
    settle grace is the guard that does not depend on which clock won.

    Mutation: drop the settle-grace branch and RED — a file with yesterday's
    name and a fresh mtime becomes a seal candidate while still being written.
    """
    live = _write(tmp_path, TODAY - timedelta(days=2), aged=False)

    assert seal.eligible(tmp_path, keep_days=0, today=TODAY) == []
    assert live.stat().st_mtime > time.time() - seal.SETTLE_GRACE_SECONDS
    aged = _write(tmp_path, TODAY - timedelta(days=3))
    assert seal.eligible(tmp_path, keep_days=0, today=TODAY) == [aged]


def test_a_zlib_corrupt_archive_is_drift_not_a_traceback(tmp_path, capsys):
    """`gzip.decompress` raises `zlib.error`, which is NOT an OSError.

    A deflate-corrupt payload (valid header, un-decodable body) used to escape
    every guard: `--check` tracebacked instead of answering drift, and `--apply`
    died mid-list. Mutation: drop `zlib.error` from the archive scan -> RED here.
    """
    day = TODAY - timedelta(days=9)
    seal.seal_one(tmp_path, _write(tmp_path, day, b'{"session_id": "z"}\n'))
    good = seal.archive_path(tmp_path, day).read_bytes()
    seal.archive_path(tmp_path, day).write_bytes(good[:10] + b"\x00\xff" * 20 + good[10:])

    assert seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--check"]) == seal.EXIT_DRIFT
    capsys.readouterr()
    assert seal.restore(tmp_path, day).startswith("no archive") is False


def test_an_unstattable_day_file_is_an_error_not_ok(tmp_path, capsys):
    """A candidate this tool cannot stat must not read as clean.

    Mutation: `eligible()`'s `except OSError: continue` without collecting into
    `unreadable` leaves `--check` at exit 0 -> RED here.
    """
    (tmp_path / "2026-01-05.jsonl").symlink_to(tmp_path / "gone.jsonl")

    assert seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--check"]) == seal.EXIT_ERROR
    capsys.readouterr()


def test_an_unverifiable_source_is_a_conflict_not_an_unlink(tmp_path, monkeypatch):
    """Fail CLOSED on the only data-loss check: a stat that fails after the read
    must keep the source.

    Mutation: the `stat_before is None` arm set to `changed = False` (its
    cycle-2 value, a tautology) REDs here.
    """
    import pathlib
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"session_id": "f"}\n')

    real_stat = pathlib.Path.stat

    def boom(self, *a, **kw):
        if self == path:
            raise OSError("stat vanished")
        return real_stat(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "stat", boom)
    result = seal.seal_one(tmp_path, path)
    monkeypatch.undo()

    assert result.status in {"conflict", "verify-failed"}, result.status
    assert not result.ok
    assert path.exists(), "an unverified source must never be unlinked"
    assert not seal.archive_path(tmp_path, day).exists()
    # The predicate itself, fail-closed: no stat means UNVERIFIED, not "same".
    assert seal._changed_since(path, None, 5) is True


def test_restore_never_replaces_a_live_day_file(tmp_path):
    """`os.replace` over a live file orphans the writer's fd: the appended
    record is lost with no error. Mutation: restore's `dst.exists()` early
    return removed -> RED (the inode is swapped under the writer).
    """
    day = TODAY
    live = _write(tmp_path, day, b'{"session_id": "live"}\n', aged=False)
    seal.seal_one(tmp_path, live)
    live.write_bytes(b'{"session_id": "live"}\n')          # same content
    # The inode of the LIVE file — captured AFTER the recreation above, so this
    # cannot pass on an inode number the filesystem recycled (#6388 cycle 3).
    before_inode = live.stat().st_ino
    sealed = seal.archive_path(tmp_path, day)

    assert seal.restore(tmp_path, day) == ""
    assert live.stat().st_ino == before_inode, "the live inode must not move"
    assert sealed.exists()


def test_a_stale_prefix_archive_is_superseded_never_accepted(tmp_path, capsys):
    """The P0 state: an archive holding a PREFIX of the source.

    Accepting it ("already archived") would unlink the only copy of the missing
    tail and report success. It must be superseded by the fuller content.
    Mutation: the stale-prefix arm returning `already-sealed` instead of falling
    through to the publish path REDs here (and silently loses the last record).
    """
    day = TODAY - timedelta(days=10)
    full = b'{"session_id": "a"}\n{"session_id": "late-append"}\n'
    stale = b'{"session_id": "a"}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(stale, mtime=0))

    result = seal.seal_one(tmp_path, path)

    assert result.status == "sealed", result
    assert gzip.decompress(target.read_bytes()) == full, "the tail must survive"
    assert not path.exists()


def test_an_interrupted_run_does_not_unlink_a_file_that_grew(tmp_path):
    """A writer that appended after the snapshot keeps its bytes: the archive is
    a strict PREFIX of the source, so it is superseded (never accepted), and the
    appended record is never dropped.

    Mutation: the stale-prefix arm returning `already-sealed` and unlinking the
    source REDs here — the appended record would be gone with it.
    """
    day = TODAY - timedelta(days=9)
    body = b'{"session_id": "b"}\n'
    path = _write(tmp_path, day, body)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body, mtime=0))

    grown = body + b'{"session_id": "appended"}\n'
    path.write_bytes(grown)
    stamp = time.time() - 86400
    os.utime(path, (stamp, stamp))

    result = seal.seal_one(tmp_path, path)

    assert result.status == "sealed", result
    assert gzip.decompress(target.read_bytes()) == grown, "the append must survive"
    assert not path.exists()


def test_restore_refuses_a_file_created_while_it_was_reading(tmp_path):
    """The `dst.exists()` check is a TOCTOU; the create must be exclusive, so a
    file that appears during the archive read is never replaced (an os.replace
    there swaps the inode under a writer's fd and loses its record).

    Mutation: restore's `O_EXCL` open replaced by mkstemp+os.replace REDs here.
    """
    day = TODAY - timedelta(days=9)
    body = b'{"session_id": "r"}\n'
    seal.seal_one(tmp_path, _write(tmp_path, day, body))
    dst = tmp_path / f"{day.isoformat()}.jsonl"

    real_decompress = seal.gzip.decompress

    def racing(data):
        out = real_decompress(data)
        if not dst.exists():
            dst.write_bytes(out)  # the live file appears mid-read
        return out

    seal.gzip.decompress = racing
    try:
        verdict = seal.restore(tmp_path, day)
    finally:
        seal.gzip.decompress = real_decompress

    assert "appeared while restoring" in verdict, verdict
    assert dst.read_bytes() == body, "the racing file is never replaced"


def test_the_identical_archive_path_still_pays_the_change_test(tmp_path, monkeypatch):
    """`already-sealed` must not be a licence to unlink blind: the same change
    test every other unlink pays applies here too.

    Mutation: the identical branch's `_changed_since` gate deleted REDs here
    (measured: the two rows around this one stay green without it).
    """
    day = TODAY - timedelta(days=9)
    body = b'{"session_id": "i"}\n'
    path = _write(tmp_path, day, body)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body, mtime=0))

    monkeypatch.setattr(seal, "_changed_since", lambda *a, **k: True)
    result = seal.seal_one(tmp_path, path)

    assert result.status == "conflict", result
    assert path.exists()


def test_a_source_removed_by_a_concurrent_run_is_a_success_not_a_false_alarm(tmp_path, monkeypatch, capsys):
    """Two ``--apply`` runs (or two lanes on one box) can seal the same day: the
    peer's unlink lands between our post-publish change test and our own unlink.
    POSIX ``unlink`` answers ``ENOENT`` for a link that no longer exists, and
    that IS the goal state — reporting it as a failure reds ``--apply`` with
    "their plain copies are untouched" on a substrate where every record is
    sealed, which is the false alarm this tool exists to not be.

    The window is microseconds wide and not observable deterministically, so the
    change test is pinned open and the peer's removal is injected at the rename
    (case A) and at the archive read (case B): both shapes reach the unlink with
    the source already gone, exactly as a race leaves it.

    Mutation: ``_unlink_source``'s ``except FileNotFoundError: return False``
    deleted (the pre-fix shape, a bare ``path.unlink()`` in both arms) REDs this
    row. Both arms were measured against the mutated tool: case A reports
    ``verify-failed`` ("archive published but the source was NOT removed ... both
    copies are on disk") and makes ``main`` exit EXIT_ERROR, case B reports
    ``verify-failed`` ("archived but the source could not be removed: [Errno 2]").
    """
    # ---- case A: the PUBLISH path, through the operator's own entry point.
    day_a = TODAY - timedelta(days=9)
    a = _write(tmp_path, day_a, b'{"session_id": "race-a"}\n')
    b = _write(tmp_path, TODAY - timedelta(days=10), b'{"session_id": "race-b"}\n')

    monkeypatch.setattr(seal, "_changed_since", lambda *args, **kwargs: False)
    # The publish seam is `os.link` now (never a blind os.replace — a peer's
    # archive must not be clobbered); the race is injected at BOTH so this row
    # keeps covering the path it was written for whichever seam publishes.
    real_link, real_replace = seal.os.link, seal.os.replace

    def _peer_unlink():
        for peer in (a, b):
            if peer.exists():
                peer.unlink()  # the concurrent run's unlink, first

    def racing_link(src, dst, *args, **kwargs):
        real_link(src, dst, *args, **kwargs)
        _peer_unlink()

    def racing_replace(src, dst, *args, **kwargs):
        real_replace(src, dst, *args, **kwargs)
        _peer_unlink()

    monkeypatch.setattr(seal.os, "link", racing_link)
    monkeypatch.setattr(seal.os, "replace", racing_replace)

    result = seal.seal_one(tmp_path, a)
    assert result.status == "sealed", result
    assert result.ok
    assert "already gone" in result.detail, result.detail
    assert not a.exists()
    assert gzip.decompress(seal.archive_path(tmp_path, day_a).read_bytes()) == (
        b'{"session_id": "race-a"}\n')

    code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--apply"])
    assert code == seal.EXIT_OK, code
    capsys.readouterr()
    monkeypatch.undo()

    # ---- case B: the ALREADY-SEALED arm (finishing an interrupted run).
    day_c = TODAY - timedelta(days=11)
    c = _write(tmp_path, day_c, b'{"session_id": "race-c"}\n')
    seal.archive_path(tmp_path, day_c).parent.mkdir(parents=True, exist_ok=True)
    seal.archive_path(tmp_path, day_c).write_bytes(
        gzip.compress(b'{"session_id": "race-c"}\n', mtime=0))

    monkeypatch.setattr(seal, "_changed_since", lambda *args, **kwargs: False)
    real_decompress = gzip.decompress

    def racing_decompress(data):
        out = real_decompress(data)
        if c.exists():
            c.unlink()  # a peer lane finished this day first
        return out

    monkeypatch.setattr(seal.gzip, "decompress", racing_decompress)
    second = seal.seal_one(tmp_path, c)

    assert second.status == "already-sealed", second
    assert second.ok
    assert not c.exists()


def test_an_unreadable_archive_dir_is_not_ok(tmp_path, capsys):
    """`pathlib.glob` swallows the scandir OSError, so an unreadable archive dir
    answered "ok". The alarm must answer about what it could NOT see.

    Mutation: enumerating with `Path.glob` instead of `os.scandir` REDs here.
    """
    day = TODAY - timedelta(days=9)
    seal.seal_one(tmp_path, _write(tmp_path, day, b'{"session_id": "u"}\n'))
    if os.geteuid() == 0:  # pragma: no cover - root ignores mode 000
        pytest.skip("root ignores mode 000")
    archive = tmp_path / seal.ARCHIVE_DIRNAME
    os.chmod(archive, 0o000)
    try:
        code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                          "--keep-days", "7", "--check"])
    finally:
        os.chmod(archive, 0o755)
    capsys.readouterr()

    assert code == seal.EXIT_ERROR, code


def test_an_unreadable_root_is_not_ok_and_not_silent(tmp_path, capsys):
    """`Path.glob` swallows the scandir OSError, so an unreadable substrate read
    as "nothing to do" — for BOTH modes.

    Mutation: `eligible`'s `os.scandir` replaced by `root.glob("*.jsonl")` REDs
    here (measured: --check exits 0 and --apply reports success over records it
    never saw).
    """
    if os.geteuid() == 0:  # pragma: no cover - root ignores mode 000
        pytest.skip("root ignores mode 000")
    _write(tmp_path, TODAY - timedelta(days=30))
    os.chmod(tmp_path, 0o100)  # searchable, not readable
    try:
        check = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                           "--keep-days", "7", "--check"])
        apply_code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                                "--keep-days", "7", "--apply"])
    finally:
        os.chmod(tmp_path, 0o755)
    capsys.readouterr()

    assert check == seal.EXIT_ERROR, check
    assert apply_code == seal.EXIT_ERROR, apply_code


def test_an_archive_that_cannot_be_read_does_not_abort_the_run(tmp_path, capsys):
    """`exists()` is not total: EACCES on the archive must yield a verdict, not
    a traceback that abandons every remaining day.

    Mutation: restoring the `target.exists()` gate REDs here (measured:
    PermissionError escapes into main's list comprehension).
    """
    if os.geteuid() == 0:  # pragma: no cover
        pytest.skip("root ignores mode 000")
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"session_id": "p"}\n')
    archive = tmp_path / seal.ARCHIVE_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    # IDENTICAL content, so a READABLE archive takes the `already-sealed` arm and
    # unlinks the source: `conflict` then becomes reachable ONLY when the read is
    # actually denied. With differing content this row passed whether the mode
    # took effect or not (#6388 review cycle 5).
    seal.archive_path(tmp_path, day).write_bytes(
        gzip.compress(path.read_bytes(), mtime=0))
    os.chmod(archive, 0o000)
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        os.chmod(archive, 0o755)
    capsys.readouterr()

    assert result.status == "conflict", result
    assert "unreadable" in result.detail, result.detail
    assert path.exists(), "a denied read must never cost the source"


def test_a_failed_post_removal_fsync_still_reports_the_truth(tmp_path, monkeypatch):
    """The `already-sealed` arm's post-unlink fsync used to escape AFTER the
    source was deleted, aborting the rest of `--apply` with no verdict.

    Mutation: that `except OSError` wrap removed REDs here (measured by the
    cycle-5 reviewer: the verdict body was never executed by any row).
    """
    day = TODAY - timedelta(days=9)
    body = b'{"session_id": "y"}\n'
    path = _write(tmp_path, day, body)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(body, mtime=0))

    def boom(_path):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(seal, "_fsync_dir", boom)
    result = seal.seal_one(tmp_path, path)

    assert result.status == "already-sealed", result
    assert result.ok
    assert not path.exists()
    assert "fsync" in result.detail, result.detail


def test_a_source_a_peer_removed_before_our_READ_is_success_not_an_error(tmp_path):
    """The window the peer's `_unlink_source` fix does NOT cover: the source is
    gone before our read/stat, so there is nothing to read and nothing to keep.
    The day IS sealed; reporting failure prints a false "plain copies are
    untouched".

    Mutation: the read arm's `except FileNotFoundError` removed (folded back
    into the `OSError` arm) REDs here with `verify-failed`.
    """
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"session_id": "g"}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    path.unlink()  # the peer won the whole race

    result = seal.seal_one(tmp_path, path)

    assert result.status == "already-sealed", result
    assert result.ok
    assert "already gone" in result.detail


def test_apply_does_not_claim_absence_over_a_settle_deferred_file(tmp_path, capsys):
    """The settle grace is the second exclusion that can empty `pending`; the
    message must say so instead of asserting no old day file exists.

    Mutation: reporting `deferred` dropped REDs here on the false sentence.
    """
    _write(tmp_path, TODAY - timedelta(days=30), aged=False)  # old name, fresh mtime

    code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                      "--keep-days", "7", "--apply"])
    err = capsys.readouterr().err

    assert code == seal.EXIT_OK
    assert "settle grace" in err, err
    assert "no plain day file older than" not in err


def test_absence_never_publishes_over_a_newer_peer_archive(tmp_path, monkeypatch):
    """The P0 window: a peer appends+seals+unlinks WHILE we compress. Our stale
    snapshot must never replace the peer's fuller archive — the absent source is
    not evidence about what the ARCHIVE holds.

    Mutation: `os.link(tmp, target)` replaced by `os.replace(tmp, target)` (the
    cycle-6 shape) REDs here: the peer's tail is lost, unalarmed.
    """
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"n": 1}\n')
    target = seal.archive_path(tmp_path, day)
    full = b'{"n": 1}\n{"n": 2}\n'

    real_compress = seal.gzip.compress

    def racing(data, mtime=0):
        if not target.exists():  # the peer finishes the day mid-compress
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(real_compress(full, mtime=0))
            path.unlink()
        return real_compress(data, mtime=mtime)

    monkeypatch.setattr(seal.gzip, "compress", racing)
    result = seal.seal_one(tmp_path, path)
    monkeypatch.undo()

    assert gzip.decompress(target.read_bytes()) == full, "the tail must survive"
    assert result.ok, result


def test_absence_without_a_readable_archive_is_not_success(tmp_path):
    """Success on absence is a claim about the ARCHIVE, so it must be checked.

    Mutation: `_already_gone` returning `already-sealed` without decompressing
    REDs both arms (absent archive and corrupt archive).
    """
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"session_id": "n"}\n')
    path.unlink()  # gone, and nothing archived

    absent = seal.seal_one(tmp_path, path)
    assert not absent.ok, absent
    assert "nowhere" in absent.detail, absent.detail

    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"not gzip at all")
    corrupt = seal.seal_one(tmp_path, path)
    assert not corrupt.ok, corrupt


def test_the_source_present_publish_cannot_clobber_a_peer_archive(tmp_path, monkeypatch):
    """The SECOND publish path: the source is still present when we check, so the
    change test passes, and a peer finishes (append + seal + unlink) before our
    publish. A blind `os.replace` there replaces the peer's NEWER archive with
    our stale snapshot and reports `sealed` (#6388 review, P0).

    Mutation: `_publish_no_clobber`'s `os.link` first attempt replaced by a bare
    `os.replace(tmp, target)` REDs here — the tail is lost, unalarmed.
    """
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"n": 1}\n')
    target = seal.archive_path(tmp_path, day)
    full = b'{"n": 1}\n{"n": 2}\n'

    real_link = seal.os.link

    def racing_link(src, dst, *args, **kwargs):
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(gzip.compress(full, mtime=0))
            path.unlink()  # the peer finished the day first
        return real_link(src, dst, *args, **kwargs)

    monkeypatch.setattr(seal.os, "link", racing_link)
    result = seal.seal_one(tmp_path, path)
    monkeypatch.undo()

    assert gzip.decompress(target.read_bytes()) == full, "the peer's tail survives"
    assert result.ok, result


def test_the_copy_branch_pins_the_inode_it_compares(tmp_path):
    """On a filesystem with no hard links the aside is a COPY, so the staged
    `st` is not pinned by any name: a peer's replacement file can reuse the freed
    inode number and a "verified" unlink then deletes the peer's archive. The
    caller now holds the descriptor while it compares.

    REGRESSION PIN, not a mutation proof: the collision needs the kernel to hand
    the peer's replacement the freed inode number, which this test cannot
    force deterministically — the mutation (`_preserve_aside` returning
    `(staged, -1)` on the copy branch) was measured to stay green here. It pins
    the intended behaviour (a peer's archive survives, the verdict is a
    conflict); the pinning itself is verified by reading the code and by
    `test_a_failed_create_never_deletes_a_peers_archive`, whose mutation does
    RED.
    """
    day = TODAY - timedelta(days=9)
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"n": 1}\n', mtime=0))
    peer = gzip.compress(b'{"peer": 9}\n', mtime=0)

    real_link, real_fsync = seal.os.link, seal.os.fsync

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    calls = []

    def peer_after_the_copy(fd):
        calls.append(fd)
        if len(calls) == 1:            # the temp file created by seal_one
            return
        if len(calls) == 2:            # the aside copy is now durable
            target.unlink()
            target.write_bytes(peer)
        return

    seal.os.link, seal.os.fsync = no_links, peer_after_the_copy
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.fsync = real_link, real_fsync

    assert result.status == "conflict", result
    assert gzip.decompress(target.read_bytes()) == b'{"peer": 9}\n'


def test_an_empty_archive_is_never_proof_that_a_day_was_sealed(tmp_path):
    """`gzip.decompress(b"")` returns b"" without raising, so a zero-length
    archive must not count as "the records are somewhere".

    Mutation: `_read_archive` losing its `if not raw: return None` REDs here
    (`--check` exits 0 and the day reads as already sealed) — verified.
    """
    day = TODAY - timedelta(days=3)
    path = _write(tmp_path, day, b'{"n": 1}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"")
    path.unlink()                       # the source is gone; only the archive remains

    result = seal.seal_one(tmp_path, path)

    assert result.status == "verify-failed", result
    assert "exist nowhere" in result.detail
    assert seal._read_archive(target) is None
    assert target in seal._unreadable_archives(tmp_path)


def test_an_unpublished_name_taken_says_the_bytes_exist_only_in_the_source(tmp_path):
    """When even `.unpublished-<sha8>` is taken, the verdict must not name a file
    that holds nothing — it must say where the bytes actually are.

    This row REDs with a `NameError` on the pre-fix shape (the flag the message
    needs was a `seal_one` local), which crashed the whole `--apply` loop.
    """
    day = TODAY - timedelta(days=9)
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"n": 1}\n', mtime=0))
    digest = seal._sha256(full)
    (target.parent / f"{target.name}.unpublished-{digest[:8]}").write_bytes(b"taken")

    real_link, real_unlink = seal.os.link, seal.os.unlink

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    def third_run_wins(p, *a, **k):
        result = real_unlink(p, *a, **k)
        if Path(p) == target:
            target.write_bytes(gzip.compress(b'{"third": 3}\n', mtime=0))
        return result

    seal.os.link, seal.os.unlink = no_links, third_run_wins
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.unlink = real_link, real_unlink

    assert result.status == "conflict", result
    assert "only in the plain source" in result.detail, result.detail


def test_a_peer_that_publishes_during_the_supersede_is_not_overwritten(tmp_path):
    """The aside step and the unlink of the canonical name are two operations: a
    peer can publish in the gap. Its archive is NEWER than the bytes we staged,
    so it must be left alone and the verdict must be a conflict, not `sealed`.

    Mutation: `_remove_if_same(target, staged)` replaced by a bare `os.unlink`
    REDs here (the peer's archive is deleted and the status is `sealed`).
    """
    day = TODAY - timedelta(days=9)
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"n": 1}\n', mtime=0))
    peer = gzip.compress(b'{"peer": 9}\n', mtime=0)

    real_link = seal.os.link

    def peer_in_the_gap(src, dst, *a, **k):
        result = real_link(src, dst, *a, **k)
        if ".superseded-" in Path(dst).name:
            # The moment AFTER the stale archive is preserved and BEFORE the
            # canonical name is dropped: a peer replaces it with its own archive.
            target.unlink()
            target.write_bytes(peer)
        return result

    seal.os.link = peer_in_the_gap
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link = real_link

    assert result.status == "conflict", result
    assert gzip.decompress(target.read_bytes()) == b'{"peer": 9}\n'


def test_a_failed_create_never_deletes_a_peers_archive(tmp_path):
    """The cleanup after a failed exclusive create must remove OUR inode only:
    a peer can replace the canonical path inside that window, and an unqualified
    unlink would delete the peer's verified archive.

    Mutation: `_remove_if_ours` replaced by a bare `os.unlink` REDs here.
    """
    day = TODAY - timedelta(days=3)
    path = _write(tmp_path, day, b'{"n": 1}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    peer = gzip.compress(b'{"peer": 2}\n', mtime=0)

    real_link, real_fsync = seal.os.link, seal.os.fsync

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    calls = []

    def peer_takes_over(fd):
        calls.append(fd)
        if len(calls) == 1:
            return                      # the temp file: let it through
        # Inside the create window: the peer replaces the path with its archive.
        with contextlib.suppress(OSError):
            os.unlink(target)
        target.write_bytes(peer)
        raise OSError(errno.ENOSPC, "no space left")

    seal.os.link, seal.os.fsync = no_links, peer_takes_over
    try:
        seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.fsync = real_link, real_fsync

    assert target.exists(), "the peer's archive must never be deleted"
    assert gzip.decompress(target.read_bytes()) == b'{"peer": 2}\n'


def test_a_run_that_loses_the_gap_keeps_its_bytes_on_a_no_link_filesystem(tmp_path):
    """The `.unpublished-<sha8>` copy is the ONLY place a gap loser's bytes land,
    so it must use the same no-hard-link fallback and the verdict must not claim
    a copy that does not exist.

    Mutation: the `_publish_bytes` call replaced by a bare, suppressed `os.link`
    REDs the `lost.exists()` assertion.
    """
    day = TODAY - timedelta(days=9)
    stale = b'{"n": 1}\n'
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(stale, mtime=0))

    real_link, real_unlink = seal.os.link, seal.os.unlink

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    def third_run_wins(p, *a, **k):
        result = real_unlink(p, *a, **k)
        if Path(p) == target:            # the peer publishes the instant we move aside
            target.write_bytes(gzip.compress(b'{"third": 3}\n', mtime=0))
        return result

    seal.os.link, seal.os.unlink = no_links, third_run_wins
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.unlink = real_link, real_unlink

    assert result.status == "conflict", result
    lost = [p for p in target.parent.iterdir() if ".unpublished-" in p.name]
    assert lost, result.detail
    assert gzip.decompress(lost[0].read_bytes()) == full
    assert str(lost[0].name) in result.detail


def test_a_torn_aside_is_removed_not_left_as_a_preserved_archive(tmp_path):
    """A half-copied `.superseded-*` file is indistinguishable from a preserved
    archive and `--check` cannot see it (its name does not end in `.jsonl.gz`),
    so the copy removes its own partial before the error propagates.

    Mutation: the `_remove_if_ours(aside, mine)` in `_preserve_aside` removed
    REDs here.
    """
    day = TODAY - timedelta(days=9)
    path = _write(tmp_path, day, b'{"n": 1}\n{"n": 2}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"n": 1}\n', mtime=0))

    real_link, real_fsync = seal.os.link, seal.os.fsync

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    calls = []

    def dies_mid_copy(fd):
        calls.append(fd)
        if len(calls) >= 2:             # 1 = temp file, 2 = the aside copy
            raise OSError(errno.ENOSPC, "no space left")

    seal.os.link, seal.os.fsync = no_links, dies_mid_copy
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.fsync = real_link, real_fsync

    assert result.status == "conflict", result
    assert not [p for p in target.parent.iterdir() if ".superseded-" in p.name]
    assert target.exists() and gzip.decompress(target.read_bytes()) == b'{"n": 1}\n'
    assert path.exists(), "the source is never touched on a failed seal"


def test_a_no_hard_link_filesystem_still_supersedes_a_stale_prefix(tmp_path):
    """exFAT/SMB/FAT/overlay have no hard links. The fallback must cover the
    SUPERSEDE too, not just the plain create: otherwise moving the stale archive
    aside leaves the canonical path missing and `--apply` errors forever.

    Mutation: `_preserve_aside`/`_publish_bytes` reduced to a bare `os.link`
    REDs here (no archive at the canonical path, status "conflict").
    """
    day = TODAY - timedelta(days=9)
    stale = b'{"n": 1}\n'
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(stale, mtime=0))

    real_link = seal.os.link

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    seal.os.link = no_links
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link = real_link

    assert result.status == "sealed", result
    assert target.exists(), "the canonical archive must exist on a no-link FS"
    assert gzip.decompress(target.read_bytes()) == full
    asides = [p for p in target.parent.iterdir() if ".superseded-" in p.name]
    assert asides and gzip.decompress(asides[0].read_bytes()) == stale


def test_a_failed_exclusive_create_leaves_no_partial_archive(tmp_path):
    """A torn write in the O_EXCL fallback must not strand a truncated archive
    at the canonical path: that is a red no run could ever clear.

    Mutation: the `os.unlink(target)` in `_create_exclusive`'s except removed
    REDs here (a 10-byte archive survives).
    """
    day = TODAY - timedelta(days=3)
    path = _write(tmp_path, day, b'{"n": 1}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)

    real_link, real_fsync = seal.os.link, seal.os.fsync

    def no_links(*a, **k):
        raise OSError(errno.ENOTSUP, "no hard links here")

    calls = []

    def full_disk(fd):
        # The FIRST fsync is the temp file (a safe failure: the source is kept).
        # The SECOND lands inside the exclusive create of the canonical archive,
        # which is the torn write under test.
        calls.append(fd)
        if len(calls) > 1:
            raise OSError(errno.ENOSPC, "no space left")

    seal.os.link = no_links
    seal.os.fsync = full_disk
    try:
        result = seal.seal_one(tmp_path, path)
    finally:
        seal.os.link, seal.os.fsync = real_link, real_fsync

    assert result.status == "conflict", result
    assert "no space left" in result.detail
    assert not target.exists(), "a half-written archive must be removed"
    assert path.exists(), "the plain source is never touched"


def test_a_stale_prefix_is_moved_aside_not_replaced(tmp_path):
    """Superseding a stale prefix must not DESTROY the peer's archive: the old
    copy stays on disk under a `.superseded-<sha8>` name, so a read-then-replace
    race can never leave a record only in memory.

    Mutation: the aside `os.replace` + `os.link` replaced by a bare
    `os.replace(tmp, target)` (the cycle-8 shape) REDs the aside assertion.
    """
    day = TODAY - timedelta(days=9)
    stale = b'{"n": 1}\n'
    full = b'{"n": 1}\n{"n": 2}\n'
    path = _write(tmp_path, day, full)
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(stale, mtime=0))

    result = seal.seal_one(tmp_path, path)

    assert result.status == "sealed", result
    assert gzip.decompress(target.read_bytes()) == full
    asides = sorted(p for p in target.parent.iterdir()
                    if ".superseded-" in p.name)
    assert asides, "the previous archive must be kept, never replaced"
    assert gzip.decompress(asides[0].read_bytes()) == stale


def test_an_absent_day_with_no_archive_is_drift_not_a_quiet_ok(tmp_path, capsys):
    """A candidate that vanished is only a win if an archive PROVES it; with no
    archive anywhere its records exist nowhere.

    Mutation: the `unproven` filter deleted (trusting `vanished` as success)
    REDs here at exit 0.
    """
    ghost = tmp_path / f"{(TODAY - timedelta(days=30)).isoformat()}.jsonl"

    real_scandir = seal.os.scandir
    ghost.touch()

    def scan_then_remove(path):
        """List the entry, then unlink it: the scan/stat gap a peer creates."""
        entries = list(real_scandir(path))
        ghost.unlink(missing_ok=True)
        return entries

    seal.os.scandir = scan_then_remove
    try:
        code = seal.main(["--root", str(tmp_path), "--today", TODAY.isoformat(),
                          "--keep-days", "7", "--check"])
    finally:
        seal.os.scandir = real_scandir
    err = capsys.readouterr().err

    assert code == seal.EXIT_DRIFT, (code, err)
    assert "UNPROVEN ABSENCE" in err or "no readable archive" in err, err


def test_a_differing_archive_is_never_clobbered(tmp_path):
    """Two copies that disagree: NEITHER is discarded — a human decides.

    Mutation: `os.replace(tmp, target)` made unconditional (i.e. the
    differing-content early return deleted) REDs here.
    """
    day = TODAY - timedelta(days=11)
    path = _write(tmp_path, day, b'{"session_id": "new"}\n')
    target = seal.archive_path(tmp_path, day)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(gzip.compress(b'{"session_id": "old"}\n', mtime=0))

    result = seal.seal_one(tmp_path, path)

    assert result.status == "conflict"
    assert not result.ok
    assert path.exists(), "the plain copy is the only copy of the new content"
    assert gzip.decompress(target.read_bytes()) == b'{"session_id": "old"}\n'


def test_restore_round_trips_and_refuses_to_overwrite_a_differing_file(tmp_path):
    """Restoring is a read convenience for `build_window_transcript.py`; the
    archive is kept, and a differing plain file is never overwritten."""
    day = TODAY - timedelta(days=13)
    body = b'{"session_id": "d"}\n' * 3
    seal.seal_one(tmp_path, _write(tmp_path, day, body))

    assert seal.restore(tmp_path, day) == ""
    assert (tmp_path / f"{day.isoformat()}.jsonl").read_bytes() == body
    assert seal.archive_path(tmp_path, day).exists(), "restore must not un-seal"

    (tmp_path / f"{day.isoformat()}.jsonl").write_bytes(b"tampered\n")
    assert "different content" in seal.restore(tmp_path, day)
    assert (tmp_path / f"{day.isoformat()}.jsonl").read_bytes() == b"tampered\n"
    assert gzip.decompress(seal.archive_path(tmp_path, day).read_bytes()) == body


@pytest.mark.parametrize("name", ["2026-13-01.jsonl", "2026-02-30.jsonl", "x.jsonl"])
def test_a_name_that_is_not_a_real_day_is_not_ours(tmp_path, name):
    path = tmp_path / name
    path.write_text("foreign\n")
    assert seal._day_of(path) is None
    assert seal.eligible(tmp_path, keep_days=1, today=TODAY) == []
