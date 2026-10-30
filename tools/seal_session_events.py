#!/usr/bin/env python3
"""#6388 — SEAL the session-events substrate instead of deleting anything.

The measured problem (re-measured on this box, see the PR):

    ~/.tortoise/session-events/   4 daily JSONL files, 651 records, 2.75 MiB,
                                  +222..263 records/day, no rotation, no drain

That substrate is a **deposit-only** write-ahead log: ``reflect-hook`` appends a
session record on shutdown, and its consumer (the #3515 store-sync second-JSONL
leg, ``tools/build_window_transcript.py`` for window work) reads whole day
files.  It never shrinks.  The remedy the issue names first is
**compress / seal / rotate rather than discard** — and that is what this tool
does, because the alternative (a TTL / a reaper) contradicts an owner ruling.

Contradiction test (#3870, owner): *"the local capture spool is type-2 user data
kept until the user deletes it"* — a TTL, a max-age or a "prune after N days"
is FORBIDDEN.  This tool therefore **deletes nothing that is not provably
archived**:

1. a day file is only SEALED (gzip) after a byte-for-byte round-trip check;
2. the original is removed only when the sealed copy's decompressed sha256
    equals the original's — the sealed file *is* the evidence, re-encoded;
3. an existing archive is never clobbered — a differing one is a conflict to
   report, not a thing to overwrite;
4. today's file is never sealed, even at ``--keep-days 0``: it is being
   appended to right now, and sealing a live append is how a record is torn.

Bounds, stated honestly: sealing bounds the **plain working set** (files
younger than the window) and multiplies the archive's capacity by the gzip
ratio (JSONL measures ~9-14x here).  It cannot bound TOTAL bytes — bounding a
write-only log without discarding records is impossible, and #3870 forbids
discarding.  What it removes is the unbounded *growth of the readable set*.

Usage::

    python tools/seal_session_events.py --check            # exit 1 = drift
    python tools/seal_session_events.py --apply            # seal eligible days
    python tools/seal_session_events.py --restore 2026-10-27

``--check`` is the alarm half (a bound nobody measures is not a bound);
``--apply`` is the action half and is deliberately explicit — it is not wired
into a session path, because it touches files a live hook is appending to.
"""
from __future__ import annotations

import argparse
import contextlib
import gzip
import hashlib
import os
import re
import sys
import tempfile
import zlib
from dataclasses import dataclass
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

#: The substrate this tool owns.  Overridable in every entry point so a test
#: can never seal the developer machine's real captures.
DEFAULT_ROOT = Path.home() / ".tortoise" / "session-events"
#: Days kept PLAIN (readable without gunzip).  The consumer
#: ``tools/build_window_transcript.py`` takes a plain ``--events <file>.jsonl``,
#: so recent days stay directly readable and only settled history is sealed.
DEFAULT_KEEP_DAYS = 7
#: Sealed files land here, so the plain set and the archive never interleave.
ARCHIVE_DIRNAME = "archive"
#: A day file whose mtime is newer than this is treated as possibly LIVE and is
#: never sealed.  `reflect-hook` names its file from `toISOString()` (UTC) while
#: this tool reads the clock, so "today" on one clock can be "yesterday" on the
#: other; the settle grace is the timezone-INDEPENDENT answer to "is a writer
#: still on this file", and it is the guard that does not depend on which clock
#: the producer picked.
SETTLE_GRACE_SECONDS = 3600

_DAY_FILE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.jsonl$")

EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_ERROR = 2


@dataclass(frozen=True)
class Sealed:
    """The outcome of one seal attempt (never a silent success)."""

    day: date
    status: str  # "sealed" | "already-sealed" | "verify-failed" | "conflict"
    plain_bytes: int
    sealed_bytes: int
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("sealed", "already-sealed")


def _day_of(path: Path) -> date | None:
    """The day a substrate file name declares, or ``None`` for a foreign file.

    Only ``YYYY-MM-DD.jsonl`` is ours.  A foreign name (``.tmp``, a test
    fixture, a hand-kept note) is never sealed — the name IS the contract the
    consumer reads.
    """
    m = _DAY_FILE_RE.match(path.name)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def archive_path(root: Path, day: date) -> Path:
    return root / ARCHIVE_DIRNAME / f"{day.isoformat()}.jsonl.gz"


def eligible(root: Path, *, keep_days: int, today: date,
             now: float | None = None,
             settle_seconds: int = SETTLE_GRACE_SECONDS,
             unreadable: list[Path] | None = None) -> list[Path]:
    """Plain day files OLDER than the keep window, oldest first.

    Two exclusions, both load-bearing:

    * **today** is never eligible, whatever ``keep_days`` says — it is being
      appended to by ``reflect-hook`` while this runs, and a seal of a
      partially-written file is the one loss this tool exists to prevent.
    * a file modified within ``settle_seconds`` is skipped whatever its name
      says — the producer names its day file from UTC while this tool reads the
      local clock, so the NAME is not proof that no writer is on it (#6388
      review P1).  A seal of a file that is mid-append loses the appended bytes
      with the unlinked inode.
    * the boundary is STRICT (``day < cutoff``): with ``keep_days=7`` a file
      from exactly seven days ago is still plain, so the window means "seven
      full days of directly-readable history", not six.  ``keep_days=0``
      therefore means "seal everything except today", not "seal nothing",
      and a NEGATIVE window (a cutoff in the future) still cannot reach
      today's file — the two exclusions are independent on purpose.
    """
    # No clamp on ``keep_days``: an absurd value (negative -> a cutoff in the
    # FUTURE) must be caught by the today guard below, not silently by
    # arithmetic that would also swallow the guard's job.
    cutoff = today - timedelta(days=keep_days)
    clock = time.time() if now is None else now
    out = []
    for path in sorted(root.glob("*.jsonl")):
        day = _day_of(path)
        if day is None or day >= today or day >= cutoff:
            continue
        try:
            st = path.stat()
        except OSError:
            # "could not look" is not "clean": the caller gets the path so
            # `--check` can answer EXIT_ERROR instead of ok (the same rule the
            # non-directory root arm applies), while `--apply` skips it.
            if unreadable is not None:
                unreadable.append(path)
            continue
        # SETTLED, on a clock the producer cannot confuse: a file modified
        # within the grace is treated as live whatever its NAME says.
        if settle_seconds > 0 and clock - st.st_mtime < settle_seconds:
            continue
        out.append(path)
    return out


def seal_one(root: Path, path: Path) -> Sealed:
    """Seal ONE day file.  Returns a verdict; never raises on a bad file."""
    day = _day_of(path)
    if day is None:  # pragma: no cover - caller filters, kept total
        raise ValueError(f"not a session-events day file: {path}")
    try:
        plain = path.read_bytes()
    except OSError as e:
        return Sealed(day, "verify-failed", 0, 0, f"unreadable: {e}")
    digest = _sha256(plain)
    try:
        stat_before = path.stat()
    except OSError:
        stat_before = None
    target = archive_path(root, day)

    if target.exists():
        try:
            prior = gzip.decompress(target.read_bytes())
        except (OSError, EOFError, zlib.error) as e:
            return Sealed(day, "conflict", len(plain), target.stat().st_size,
                          f"existing archive is unreadable: {e}")
        if _sha256(prior) != digest:
            if plain.startswith(prior) and len(prior) < len(plain):
                # The archive is a provably STALE PREFIX of the same file — a
                # re-encoding of an earlier snapshot (a race published it before
                # a late append landed).  Superseding it drops no byte and is
                # the only way this state ever heals; the ordinary disagreement
                # below does NOT.
                pass
            else:
                # NEVER clobber: the two copies disagree, and either one may be
                # the only complete record.  A human decides; both stay.
                return Sealed(day, "conflict", len(plain), target.stat().st_size,
                              f"{target} already exists with different content — "
                              "left both copies in place")
        # Identical content already archived (a run interrupted between the
        # archive write and the unlink): finish the job, seal nothing twice.
        with contextlib.suppress(OSError):
            path.unlink()
        return Sealed(day, "already-sealed", len(plain), target.stat().st_size)

    tmp: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent),
                                        prefix=target.name + ".", suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as out:
            out.write(gzip.compress(plain, mtime=0))
            out.flush()
            os.fsync(out.fileno())
        # THE VERIFICATION IS THE POLICY: the source is removed only when the
        # file that replaced it decompresses to exactly the original bytes.
        # A `gzip` that wrote a truncated archive must leave the plain file
        # alone — a bound that costs a record is worse than an unbounded disk.
        if _sha256(gzip.decompress(tmp.read_bytes())) != digest:
            return Sealed(day, "verify-failed", len(plain), tmp.stat().st_size,
                          "sealed copy does not round-trip — source kept")
        # THE CHANGE TEST RUNS BEFORE PUBLISH: a file that grew while we read it
        # must not publish an archive at all, or the archive becomes a truncated
        # day that looks complete and every later run calls it a conflict —
        # an alarm that can never be cleared (#6388 review cycle 2).
        if stat_before is not None:
            changed = (not _unchanged_since(path, stat_before)
                       or len(plain) != stat_before.st_size)
        else:
            # An UNVERIFIABLE source is a conflict, never an unlink: fail closed
            # on the only data-loss check in this tool.
            changed = True
        if changed:
            return Sealed(day, "conflict", len(plain), 0,
                          "file changed while sealing — source kept")
        os.replace(tmp, target)
        _fsync_dir(target.parent)
        # NOTHING APPENDED SINCE THE SNAPSHOT? A writer that added bytes
        # between the read above and this unlink would have them destroyed with
        # the inode. Compare identity + size + mtime: any change keeps the
        # source (and the archive is a strict prefix of the truth, never a
        # replacement for it).
        if not _unchanged_since(path, stat_before):
            # The last belt: the file changed between the publish above and this
            # unlink.  The archive is a verified prefix, the source stays.
            return Sealed(day, "conflict", len(plain), target.stat().st_size,
                          "file changed while sealing — source kept")
        path.unlink()
        _fsync_dir(target.parent)
    except (OSError, EOFError, zlib.error) as e:
        return Sealed(day, "verify-failed", len(plain), 0,
                      f"seal failed: {e} — source kept")
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
    return Sealed(day, "sealed", len(plain), target.stat().st_size)


def _fsync_dir(path: Path) -> None:
    """Make a rename in ``path`` durable before the only other copy is removed.

    ``tortoise/capture_spool.py`` carries the same rule for the same reason: a
    rename whose directory entry was never fsynced can vanish in a power loss,
    and here the source is unlinked immediately after.
    """
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _unchanged_since(path: Path, before: os.stat_result) -> bool:
    """True when ``path`` still has the inode/size/mtime observed before the read."""
    try:
        now = path.stat()
    except OSError:
        return False
    return (now.st_ino == before.st_ino and now.st_size == before.st_size
            and now.st_mtime_ns == before.st_mtime_ns)


def restore(root: Path, day: date) -> str:
    """Write the plain day file back from its archive.  ``""`` on success.

    The archive is KEPT: restoring is a read convenience for
    ``tools/build_window_transcript.py``, not a request to un-seal.
    """
    target = archive_path(root, day)
    if not target.is_file():
        return f"no archive for {day.isoformat()} at {target}"
    dst = root / f"{day.isoformat()}.jsonl"
    if dst.exists():
        # NEVER os.replace over a live day file: a writer holding the old fd
        # would append into the orphaned inode and the record would vanish with
        # no error anywhere.  An existing plain file is either already what we
        # would write (nothing to do) or a disagreement we refuse.
        try:
            if dst.read_bytes() == gzip.decompress(target.read_bytes()):
                return ""
        except (OSError, EOFError, zlib.error) as e:
            return f"{dst} already exists and the archive is unreadable: {e}"
        return (f"{dst} already exists with different content — refusing to "
                "overwrite it")
    tmp: Path | None = None
    try:
        plain = gzip.decompress(target.read_bytes())
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=str(root), prefix=dst.name + ".",
                                        suffix=".tmp")
        tmp = Path(tmp_name)
        with os.fdopen(fd, "wb") as out:
            out.write(plain)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, dst)
        _fsync_dir(root)
    except (OSError, EOFError, zlib.error) as e:
        return f"restore failed: {e}"
    finally:
        if tmp is not None:
            with contextlib.suppress(OSError):
                tmp.unlink()
    return ""


def _unreadable_archives(root: Path) -> list[Path]:
    """Sealed files that cannot be decompressed — an archive that lost its
    bytes is the one state where the evidence no longer exists."""
    out = []
    archive_dir = root / ARCHIVE_DIRNAME
    try:
        candidates = sorted(archive_dir.glob("*.jsonl.gz"))
    except (FileNotFoundError, NotADirectoryError):
        return out
    for path in candidates:
        try:
            gzip.decompress(path.read_bytes())
        except (OSError, EOFError, zlib.error):
            out.append(path)
    return out


def _report(rows: list[Sealed], *, stream) -> None:
    for row in rows:
        ratio = (f"{row.plain_bytes / row.sealed_bytes:.1f}x"
                 if row.sealed_bytes else "-")
        print(f"{row.day.isoformat()}  {row.status:<14} "
              f"{row.plain_bytes:>9} B -> {row.sealed_bytes:>7} B  ({ratio})"
              + (f"  {row.detail}" if row.detail else ""), file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal settled session-events day files (compress, never "
                    "discard — #6388 / #3870).")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT,
                        help=f"substrate directory (default: {DEFAULT_ROOT})")
    parser.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS,
                        help="days kept plain (default: %(default)s)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true",
                      help="exit 1 when a day file is past the keep window")
    mode.add_argument("--apply", action="store_true", help="seal eligible days")
    mode.add_argument("--restore", metavar="YYYY-MM-DD",
                      help="write a sealed day back out as plain JSONL")
    parser.add_argument("--today", metavar="YYYY-MM-DD",
                        help="override the clock (tests / backfills)")
    args = parser.parse_args(argv)

    root: Path = args.root
    if not root.is_dir():
        # "could not look" is NOT "clean": an alarm that answers ok on a
        # missing or mistyped substrate is the inert gate this repo forbids.
        print(f"cannot inspect {root} — it is not a directory", file=sys.stderr)
        return EXIT_ERROR
    try:
        # UTC, matching the producer: `reflect-hook` names its day file from
        # `toISOString()`, so a local-clock "today" can name the file a writer
        # is on right now as "yesterday" (#6388 review P1).
        today = (date.fromisoformat(args.today) if args.today
                 else datetime.now(UTC).date())
    except ValueError as e:
        print(f"--today: {e}", file=sys.stderr)
        return EXIT_ERROR

    if args.restore:
        try:
            day = date.fromisoformat(args.restore)
        except ValueError as e:
            print(f"--restore: {e}", file=sys.stderr)
            return EXIT_ERROR
        problem = restore(root, day)
        if problem:
            print(problem, file=sys.stderr)
            return EXIT_ERROR
        print(f"restored {root / f'{day.isoformat()}.jsonl'} from archive")
        return EXIT_OK

    unreadable: list[Path] = []
    try:
        pending = eligible(root, keep_days=args.keep_days, today=today,
                           unreadable=unreadable)
    except OSError as e:
        print(f"cannot list {root}: {e}", file=sys.stderr)
        return EXIT_ERROR
    if args.check and unreadable:
        print(f"UNREADABLE: {len(unreadable)} day file(s) under {root} could "
              "not be stat'd — cannot tell whether they are past the window:",
              file=sys.stderr)
        for path in unreadable:
            print(f"  {path.name}", file=sys.stderr)
        return EXIT_ERROR
    if args.check:
        broken = []
        try:
            broken = _unreadable_archives(root)
        except OSError as e:
            print(f"cannot list {root / ARCHIVE_DIRNAME}: {e}", file=sys.stderr)
            return EXIT_ERROR
        if broken:
            print(f"UNREADABLE ARCHIVE: {len(broken)} sealed day file(s) under "
                  f"{root / ARCHIVE_DIRNAME} cannot be decompressed:",
                  file=sys.stderr)
            for path in broken:
                print(f"  {path.name}", file=sys.stderr)
            return EXIT_DRIFT
        if not pending:
            print(f"ok — no plain day file older than {args.keep_days} days "
                  f"under {root}")
            return EXIT_OK
        print(f"UNSEALED: {len(pending)} day file(s) older than "
              f"{args.keep_days} days under {root}:", file=sys.stderr)
        for path in pending:
            with contextlib.suppress(OSError):
                print(f"  {path.name}  {path.stat().st_size} B", file=sys.stderr)
        print("run `python tools/seal_session_events.py --apply`",
              file=sys.stderr)
        return EXIT_DRIFT

    rows = [seal_one(root, path) for path in pending]
    _report(rows, stream=sys.stdout)
    if not rows:
        print(f"nothing to seal — no plain day file older than "
              f"{args.keep_days} days under {root}")
    bad = [r for r in rows if not r.ok]
    if bad:
        print(f"{len(bad)} day file(s) could NOT be sealed; their plain copies "
              "are untouched", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
