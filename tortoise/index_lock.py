"""Per-session indexing lock — flock+PID hybrid (#280 item 1).

Unix-only (flock + O_NOFOLLOW + fchmod): on platforms lacking these
(fcntl/O_NOFOLLOW/fchmod absent), lock operations raise (AttributeError/
ImportError) — callers treat them as retryable, never fatal (#280 review P3).

Protects the LOCAL session-indexing read-modify-write cycle
(MATCH → compute merged state → SET, which is NOT MERGE-atomic) from
concurrent writers on the same session file: the fire-and-forget
session-end hook, a manual `session_indexer` CLI run, and the
reconciliation sweep.

Mechanism (align decision P3, issue #280):
  - fcntl.flock (non-blocking) on ``~/.tortoise/index-{session_id}.pid`` —
    kernel auto-releases on holder death incl. SIGKILL.
  - PID + timestamp written into the same file for observability
    (E2E-13: stale-lock recovery must be observable).
  - kill-0 liveness probe on the recorded PID, with the 10-minute file
    age as *attribution fallback* when the PID is unreadable: a lock
    whose holder is gone (or unidentifiable for > 10 min) is force-
    released and re-acquired. Live holders are NEVER evicted — a lock
    that is merely old but held by a live process reports as held.

The path contract from the issue is ``index-{session_id}.pid``, which
composes without collision against the extension's ``capture-{sid}.pid``.
"""
from __future__ import annotations

import os
import re
import stat
import time
from pathlib import Path

# 10 minutes — force-release threshold for stale locks (issue #280 item 1).
STALE_AGE_SECONDS = 600

# Session IDs may come from untrusted frontmatter — never let them escape
# the lock directory (path traversal). Anything outside the safe alphabet
# becomes "_" (mirrors the ingest path-validation discipline of #329).
_SAFE_SID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_session_id(session_id: str) -> str:
    """Sanitize a session ID for use in a lock filename.

    Rejects empty/whitespace (falls back to ``"session"``), strips
    path separators / traversal tokens so the ID cannot escape
    ``~/.tortoise``, and caps the length so a hostile frontmatter
    ``sessionId`` cannot raise ENAMETOOLONG from ``open()`` inside
    ``acquire()`` (which would abort the batch sweep / crash the
    session-end hook — violating the fire-and-forget exit-0 contract).
    """
    cleaned = _SAFE_SID_RE.sub("_", (session_id or "").strip())
    cleaned = cleaned.lstrip("._")  # no hidden files, no ".." traversal
    cleaned = cleaned[:128]
    return cleaned or "session"


def lock_path_for(session_id: str, lock_dir: str | os.PathLike | None = None) -> Path:
    """Resolve the per-session lock file path.

    Default directory: ``~/.tortoise`` (canonical local store). Honors
    ``TORTOISE_INDEX_LOCK_DIR`` for tests / non-home setups.
    """
    if lock_dir is None:
        lock_dir = os.environ.get("TORTOISE_INDEX_LOCK_DIR", "") or (
            Path.home() / ".tortoise")
    return Path(lock_dir) / f"index-{sanitize_session_id(session_id)}.pid"


def _pid_alive(pid: int) -> bool:
    """kill-0 liveness probe. Returns False for dead or permission-denied."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — treat as alive (conservative).
        return True
    except OSError:
        return False


class SessionIndexLock:
    """Exclusive per-session index lock (flock + PID hybrid).

    Usage::

        lock = SessionIndexLock(session_id)
        status = lock.acquire()      # "acquired" | "held" | "stale-recovered"
        if status in ("acquired", "stale-recovered"):
            try:
                ... read-compute-write ...
            finally:
                lock.release()

    Also usable as a context manager (``with SessionIndexLock(sid) as st:``).
    """

    def __init__(self, session_id: str, lock_dir: str | os.PathLike | None = None,
                 stale_age_seconds: int = STALE_AGE_SECONDS):
        self.session_id = sanitize_session_id(session_id)
        self.path = lock_path_for(session_id, lock_dir)
        self.stale_age_seconds = stale_age_seconds
        self._fh = None
        self._status: str | None = None
        self._detail: str | None = None

    # ── introspection ──────────────────────────────────────────────

    def held_by(self) -> dict:
        """Attribution read of the lock file (best-effort).

        Returns {pid, started_at, age_seconds, alive, stale} — never raises;
        missing/corrupt files yield empty fields. Reads from the open fd when
        one is held (the same inode we locked), else from the path.
        """
        info: dict = {"pid": None, "started_at": None, "age_seconds": None,
                      "alive": None, "stale": False}
        try:
            if self._fh is not None:
                self._fh.seek(0)
                raw = self._fh.read().decode(errors="replace").strip()
            else:
                raw = self.path.read_text().strip()
        except Exception:
            return info
        parts = raw.split()
        try:
            info["pid"] = int(parts[0]) if parts else None
        except ValueError:
            info["pid"] = None
        if len(parts) > 1:
            info["started_at"] = parts[1]
        try:
            if self._fh is not None:
                mtime = os.fstat(self._fh.fileno()).st_mtime
            else:
                mtime = self.path.stat().st_mtime
            info["age_seconds"] = max(0, int(time.time() - mtime))
        except Exception:
            pass
        if info["pid"] is not None:
            info["alive"] = _pid_alive(info["pid"])
        age = info["age_seconds"]
        # Stale: a dead recorded holder is stale regardless of age (kernel
        # released its flock); an UNREADABLE attribution is stale only past
        # the age threshold (attribution fallback, align P3).
        info["stale"] = bool(
            (info["pid"] is not None and info["alive"] is False)
            or (info["pid"] is None
                and age is not None and age > self.stale_age_seconds)
        )
        return info

    # ── acquire / release ──────────────────────────────────────────

    def acquire(self) -> str:
        """Acquire the per-session lock.

        Returns one of:
          - ``"acquired"``         — lock taken; caller owns the session file.
          - ``"stale-recovered"``  — a stale lock (dead/unidentifiable holder
                                     past the age threshold) was force-released
                                     and this caller now owns it.
          - ``"held"``             — a live holder owns the lock; DO NOT write.

        Raises OSError (never fatal to a batch sweep — callers record it as a
        retryable error and continue) when the lock file cannot be opened
        safely: an unwritable/blocked lock dir (EACCES/EROFS/ENOSPC/EMFILE)
        or a planted symlink at the lock path (ELOOP from O_NOFOLLOW — the
        attacker-chosen target is never opened, truncated or written).
        """
        import fcntl

        # #280 review P2 (security): open WITHOUT following symlinks and
        # create the lock dir 0700 (no other local user may plant files/sym-)
        # links inside). O_NOFOLLOW raises ELOOP on a planted symlink instead
        # of truncating an attacker-chosen file via the a+ open.
        try:
            os.makedirs(self.path.parent, mode=0o700, exist_ok=True)
            # Round-10/11: tighten pre-existing dirs — best-effort (a shared/
            # root-owned/group-owned dir the user can write but not chmod must
            # NOT turn every acquire into a retryable error / silent loss of
            # auto-reindexing; dirs we create already get 0700 via makedirs).
            try:  # noqa: SIM105
                os.chmod(self.path.parent, 0o700)
            except OSError:
                pass
            self._fh = os.fdopen(
                os.open(self.path,
                        os.O_CREAT | os.O_RDWR | os.O_APPEND | os.O_NOFOLLOW),
                "a+b", buffering=0)
            # Review follow-up: the lock file itself is private (pid +
            # timestamp observability content stays owner-only).
            try:  # noqa: SIM105
                os.fchmod(self._fh.fileno(), 0o600)
            except OSError:
                pass
        except OSError:
            self._fh = None
            raise

        # Fast path: if the flock is free, the file is ours. Report
        # "stale-recovered" when the previous attribution was stale (dead
        # holder / unreadable-and-old) — the observable E2E-13 signal.
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self._fh_matches_path():
                return self._replaced_path_held()
            info = self.held_by()
            self._write_owner()
            self._status = "stale-recovered" if info["stale"] else "acquired"
            self._detail = (
                f"stale lock recovered (attribution: {info})" if info["stale"]
                else None)
            return self._status
        except OSError:
            pass  # fall through to attribution probe

        # Not acquired — attribute the holder. flock can only be held by a
        # LIVE process (kernel releases on death), so a failed lock + dead PID
        # means the holder died mid-contention: retry once (its fd is gone).
        info = self.held_by()
        if info["pid"] is not None and info["alive"]:
            age = info["age_seconds"]
            self._status = "held"
            self._detail = (f"pid {info['pid']} alive"
                            + (f" ({age}s old)" if age is not None else ""))
            self._close_fh()
            return self._status

        # Holder unidentifiable or dead — retry once (flock released on death).
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not self._fh_matches_path():
                return self._replaced_path_held()
            self._write_owner()
            self._status = "stale-recovered"
            self._detail = f"stale lock recovered (attribution: {info})"
            return self._status
        except OSError:
            # Still contended by a live-but-unattributed holder — respect it.
            self._status = "held"
            self._detail = "held by live process (no pid attribution)"
            self._close_fh()
            return self._status

    def release(self) -> None:
        """Release the lock (idempotent) and remove the pid file (#1231).

        Graceful shutdown unlinks the lock file WHILE still holding the
        flock (TOCTOU-safe — a contender cannot acquire the inode we
        hold). The inode guard ensures we never unlink a swapped-in
        replacement path. The file now only survives a crash, where the
        reaper's stale-pid sweep (embedded_reaper.sweep_stale_index_pid_files)
        removes it — no unbounded ~/.tortoise/index-*.pid accumulation.
        """
        import fcntl

        if self._fh:
            try:
                # Unlink while holding the flock: no contender can be
                # mid-acquire on this inode, and the inode guard refuses a
                # swapped/symlinked path. Best-effort — if removal fails the
                # file is a stale record the reaper sweep collects later.
                if self._fh_matches_path():
                    try:  # noqa: SIM105
                        self.path.unlink(missing_ok=True)
                    except OSError:
                        pass
                try:  # noqa: SIM105
                    fcntl.flock(self._fh, fcntl.LOCK_UN)
                except OSError:
                    pass
                self._fh.close()
            finally:
                self._fh = None

    @property
    def status(self) -> str | None:
        return self._status

    @property
    def detail(self) -> str | None:
        return self._detail

    def force_release(self) -> bool:
        """Explicit force-release: remove a stale lock file.

        Only safe when the holder is dead — a live holder's flock survives
        file removal and would keep writing to an unlinked inode, defeating
        the lock. TOCTOU-hardened (#280 review P2): the check-then-unlink
        window is closed by taking the flock first, re-checking attribution
        while holding it, and unlink()ing while holding. A contender that
        acquires between the probe and the unlink fails our flock and is
        never evicted; a symlink/special file at the lock path is refused
        outright (O_NOFOLLOW + lstat — never act on an attacker-chosen file).
        """
        import fcntl

        try:
            st = self.path.lstat()
        except FileNotFoundError:
            return True  # nothing to remove
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            # Symlink / non-regular file: refuse — force_release must never
            # unlink through a planted symlink or touch a special file.
            return False

        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NOFOLLOW)
        except OSError:
            return False  # vanished or unwritable — nothing safe to do

        try:
            # Take the flock first: a live holder (or contender that raced
            # our lstat) fails this non-blocking flock and is never evicted.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False

            # We hold the flock, so no live process owns the lock — but the
            # file may still name a live PID (holder released the flock
            # without removing the file): never evict that conservatively.
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                raw = os.read(fd, 4096).decode(errors="replace").strip()
            except OSError:
                raw = ""
            parts = raw.split()
            try:
                pid = int(parts[0]) if parts else None
            except ValueError:
                pid = None
            if pid is not None and _pid_alive(pid):
                return False

            # Verify the inode we locked is still the file at the path (a
            # contender may have swapped it between open and flock); if it
            # changed, someone else owns the path — back off.
            try:
                st_fd = os.fstat(fd)
                st_path = self.path.stat()
                if (st_fd.st_ino, st_fd.st_dev) != (st_path.st_ino,
                                                    st_path.st_dev):
                    return False
            except FileNotFoundError:
                return True  # path vanished — nothing left to remove
            except OSError:
                return False  # stat failed (EACCES...) — cannot verify; refuse

            # Unlink WHILE holding the flock: no contender can acquire the
            # path between our check and the removal.
            try:
                self.path.unlink(missing_ok=True)
                return True
            except OSError:
                return False
        finally:
            try:  # noqa: SIM105
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:  # noqa: SIM105
                os.close(fd)
            except OSError:
                pass

    # ── internals ──────────────────────────────────────────────────

    def _fh_matches_path(self) -> bool:
        """True when the open lock fd still refers to the file at self.path.

        Review follow-up (P2 double-hold): a force_release/contender cycle
        can unlink the lock file between our open() and flock(); the flock
        then succeeds on a dead inode — trusting it would let us write to an
        unlinked file and double-hold with a fresh acquirer who owns the new
        inode. stat() follows symlinks, so a swapped-in symlink also trips
        the guard (never trust a path we did not lock).
        """
        if self._fh is None:
            return False
        try:
            st_fd = os.fstat(self._fh.fileno())
            st_path = self.path.stat()
        except OSError:
            return False
        return (st_fd.st_ino, st_fd.st_dev) == (st_path.st_ino,
                                                st_path.st_dev)

    def _replaced_path_held(self) -> str:
        """Map an inode-mismatched acquire to a conservative 'held' (retry
        next sweep) — never claim a lock whose path no longer resolves to the
        locked inode."""
        self._close_fh()
        self._status = "held"
        self._detail = "lock path replaced during acquire (inode mismatch)"
        return self._status

    def _write_owner(self) -> None:
        self._fh.seek(0)
        self._fh.truncate()
        # O_APPEND file — truncate() emptied the file, so the write lands at
        # offset 0 (append-at-end-of-empty == overwrite).
        self._fh.write(f"{os.getpid()} {time.time():.0f}\n".encode())
        self._fh.flush()

    def _close_fh(self) -> None:
        if self._fh:
            try:  # noqa: SIM105
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    # ── context manager ────────────────────────────────────────────

    def __enter__(self) -> "SessionIndexLock":  # noqa: UP037
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
