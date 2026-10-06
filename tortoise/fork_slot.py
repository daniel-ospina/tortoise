"""Embedded-lane ``GRAPH.COPY`` module-fork wedge: detection + recovery (#3845).

Root cause (measured, not inferred)
-----------------------------------
FalkorDB's ``_Graph_Copy`` (``src/commands/cmd_copy.c``) acquires a graph READ
lock, then ``fork()``s a child. The child runs ``encode_graph`` ->
``GraphContext_Rename`` -> ``fopen(path)`` -> ``RedisModule_Log("dump graph")``
-> ``RdbSaveGraph_latest``. ``RedisModule_Log`` reaches
``serverLogRaw`` -> ``strftime_l`` -> ``tzsetwall_basic``, which on macOS takes
a PROCESS-GLOBAL read-write lock around the timezone database.

If ANY other thread in the multi-threaded redis-server held that rwlock at the
instant ``fork()`` ran, the child inherits the lock **held** and blocks forever
on ``__psynch_rw_rdlock``. Captured on a hung ``redis-module-fork`` child
(macOS, falkordb 4.18.3 / redis 8.6.2, ``sample``):

    Cron_Run -> _Graph_Copy -> RM_Log -> moduleLogRaw -> serverLogRaw
      -> strftime_l -> tzsetwall_basic -> _pthread_rwlock_lock_slow
      -> __psynch_rw_rdlock          (blocked, 0.00s CPU, alive for minutes)

The child never exits, so Redis's ``server.child_pid`` is never cleared
(``hasActiveChildProcess()`` stays true). Redis refuses every later
``redisFork`` with ``errno == EEXIST`` (``src/server.c``
``isMutuallyExclusiveChildType``) and FalkorDB replies
``GRAPH.COPY failed, could not fork`` — permanently, for that server's life.
A restart was previously the only cure.

This is a FalkorDB / macOS-libsystem defect, NOT ours and NOT memory pressure
(``kern.maxprocperuid`` is not reached; the refusal is Redis's single-fork-slot
rule). What IS ours is defensive recovery: the server stays otherwise healthy
(``PING``/``GRAPH.QUERY`` answer), so the wedge is recoverable from the client.

Recovery (validated end-to-end)
-------------------------------
``kill -9`` the hung ``redis-module-fork`` child. Redis's ``checkChildrenDone``
(``serverCron``) ``waitpid``s it, calls ``ModuleForkDoneHandler`` and then
``resetChildState()`` — clearing ``child_pid``/``child_type`` and releasing the
slot. Measured against a live wedged server: before the kill every
``GRAPH.COPY`` returned ``could not fork``; after the kill (and reap) the very
next ``GRAPH.COPY`` returned ``OK``.

Only children of our OWN embedded daemon are targeted: the match requires the
``redis-module-fork`` process title AND the server's own unix socket path. A
BGSAVE child (``redis-rdb-bgsave``) or another server's child can never match,
so no persistence snapshot is ever killed.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

#: Substrings that identify Redis/FalkorDB refusing a ``RedisModule_Fork``.
#: ``could not fork`` is FalkorDB's reply (``cmd_copy.c``); ``Can't fork for
#: module`` is redis's own log line (``module.c``); ``cannot fork`` is a
#: defensive spelling the engine has used historically.
_FORK_REFUSAL_MARKERS = (
    "could not fork",
    "can't fork for module",
    "cannot fork for module",
)

#: Process title Redis sets on a forked module (GRAPH.COPY) child. Distinct
#: from ``redis-rdb-bgsave`` / ``redis-aof-rewrite`` — this is the only child
#: type that holds the module-fork slot and, on macOS, can deadlock.
_MODULE_FORK_PROC = "redis-module-fork"


def is_fork_refusal(exc: BaseException | None) -> bool:
    """True when ``exc`` — or its ``__cause__``/``__context__`` chain — is Redis
    refusing a module fork because another child already holds the fork slot.

    The chain is walked because redis-py can mask the originating response
    behind a transport error (``I/O operation on closed file`` after a read
    timeout); the refusal is the truthful signal wherever it sits.
    """
    seen = 0
    while exc is not None and seen < 10:
        msg = str(exc).lower()
        if any(marker in msg for marker in _FORK_REFUSAL_MARKERS):
            return True
        exc = exc.__cause__ or exc.__context__
        seen += 1
    return False


class ForkSlotWedgedError(RuntimeError):
    """The server's module-fork slot is held by a hung child and is NOT free.

    Deliberately distinct from a copy failure or a slow copy: the server is
    healthy, a fork child is wedged, and every later ``GRAPH.COPY`` will be
    refused until the child is reaped.
    """

    def __init__(self, *, site: str, dst_name: str,
                 recovery: ForkSlotRecovery | None = None) -> None:
        self.site = site
        self.dst_name = dst_name
        self.recovery = recovery or ForkSlotRecovery(wedged=True)
        detail = self.recovery.detail or "no recovery attempted"
        super().__init__(
            f"{site}: fork slot wedged — {dst_name} NOT copied; {detail}"
        )


@dataclass
class ForkSlotRecovery:
    """Outcome of one fork-slot recovery attempt."""

    wedged: bool = False
    recovered: bool = False
    killed_pids: list[int] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "wedged": self.wedged,
            "recovered": self.recovered,
            "killed_pids": list(self.killed_pids),
            "detail": self.detail,
        }


_ETIME_RE = re.compile(
    r"^(?:(?P<days>\d+)-)?(?:(?P<hours>\d+):)?(?P<minutes>\d+):(?P<seconds>\d+)$"
)


def _parse_etime(text: str) -> float | None:
    """Parse ``ps -o etime=`` (``[[dd-]hh:]mm:ss``) into seconds.

    Returns ``None`` when the field is unparseable — callers treat that as
    "age unknown", which never disqualifies a candidate (fail toward
    recovery, never toward leaving the slot wedged).
    """
    m = _ETIME_RE.match((text or "").strip())
    if not m:
        return None
    days = int(m.group("days") or 0)
    hours = int(m.group("hours") or 0)
    return (
        days * 86400.0 + hours * 3600.0
        + int(m.group("minutes")) * 60.0 + int(m.group("seconds"))
    )


def _list_processes() -> list[tuple[int, int, float | None, str]]:
    """``(pid, ppid, age_s, command)`` for every process, or ``[]``.

    ``ps`` is the only portable surface for "what did Redis name the fork
    child": Redis rewrites the child's process title to ``redis-module-fork
    unixsocket:<path>``, which is exactly what makes the orphan identifiable.
    """
    try:
        out = subprocess.run(
            # -ww: unlimited width (#4070). Without it `ps` truncates `command`
            # to the reporting width (COLUMNS, or the terminal), which cuts the
            # socket path off a long title and makes a genuinely hung child
            # invisible to the `socket_path not in command` filter below — a
            # wedge scanner must not depend on the width. GNU ps and macOS/BSD ps
            # both read `-ww` as "as many columns as necessary".
            ["ps", "-A", "-ww", "-o", "pid=,ppid=,etime=,command="],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout
    except Exception:
        # ps missing/hung, or the platform has no ps: recovery is best-effort.
        return []
    procs: list[tuple[int, int, float | None, str]] = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) < 4:
            continue
        pid_s, ppid_s, etime_s, command = parts
        try:
            pid, ppid = int(pid_s), int(ppid_s)
        except ValueError:
            continue
        procs.append((pid, ppid, _parse_etime(etime_s), command))
    return procs


def socket_path_of(db) -> str | None:
    """The unix socket path of an EMBEDDED falkordb handle, else ``None``.

    ``None`` means "not an embedded unix-socket server" (docker/remote host,
    or a test double) — recovery is then impossible and must be skipped, never
    guessed. ``path`` is redis-py's key; ``unix_socket_path`` is the
    ``Redis(...)`` spelling, checked for robustness.
    """
    for obj in (db, getattr(db, "connection", None), getattr(db, "client", None)):
        if obj is None:
            continue
        pool = getattr(obj, "connection_pool", None)
        kwargs = getattr(pool, "connection_kwargs", None) if pool is not None else None
        if not kwargs:
            continue
        for key in ("path", "unix_socket_path"):
            value = kwargs.get(key)
            if value:
                return str(value)
    return None


def server_pid_of(db) -> int | None:
    """The embedded daemon's pid, when the handle exposes it (redislite does)."""
    for obj in (db, getattr(db, "connection", None), getattr(db, "client", None)):
        pid = getattr(obj, "pid", None)
        if isinstance(pid, int) and pid > 0:
            return pid
    return None


def find_hung_module_fork_children(db, *, min_age_s: float = 0.0) -> list[tuple[int, float | None]]:
    """Hung ``redis-module-fork`` children belonging to ``db``'s daemon.

    A candidate must (a) carry the module-fork process title and (b) name this
    server's own unix socket. The socket match is what scopes recovery to our
    daemon: another server's child, or a ``redis-rdb-bgsave`` snapshot child,
    can never match.
    """
    socket_path = socket_path_of(db)
    if not socket_path:
        return []
    hits: list[tuple[int, float | None]] = []
    for pid, _ppid, age, command in _list_processes():
        if _MODULE_FORK_PROC not in command:
            continue
        if socket_path not in command:
            continue
        if age is not None and age < min_age_s:
            continue
        hits.append((pid, age))
    return hits


def fork_slot_is_wedged(db, *, min_age_s: float = 5.0) -> bool:
    """Evidence-based wedge test: a module-fork child of our daemon is lingering.

    A healthy ``GRAPH.COPY`` child encodes and exits in well under this age; a
    child that outlives it is the wedge fingerprint. The test does NOT inspect
    the exception — it only asks whether one of our module-fork children is
    still lingering. Its callers do the exception reading: the restore path
    re-raises its own ``RestoreCopyTimeoutError`` before ever consulting this
    function (#3813), so a restore timeout is never classified here; every
    OTHER copy failure at the restore sites is classified here. (Those sites
    always pass ``copy=``, so ``_graph_copy_or_diagnose``'s ``copy is None``
    default is retained for future callers and is not reached in production.)
    """
    return bool(find_hung_module_fork_children(db, min_age_s=min_age_s))


def recover_fork_slot(
    db,
    *,
    min_age_s: float = 2.0,
    timeout_s: float = 8.0,
    poll_s: float = 0.1,
) -> ForkSlotRecovery:
    """Release a wedged module-fork slot by killing our own hung fork child(ren).

    ``SIGKILL`` the hung children, then wait until Redis has reaped them (the
    process disappears from ``ps`` — that IS the proof the slot was released:
    ``checkChildrenDone`` calls ``resetChildState()`` only for the reaped
    ``child_pid``). Returns a :class:`ForkSlotRecovery`; ``recovered`` is True
    only when children were killed AND none remain.
    """
    if not socket_path_of(db):
        return ForkSlotRecovery(
            wedged=True, recovered=False,
            detail="not an embedded unix-socket server — cannot reap a hung fork child",
        )

    killed: list[int] = []
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        for pid, _age in find_hung_module_fork_children(db, min_age_s=min_age_s):
            if pid in killed:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue  # raced its own exit
            except OSError as e:
                return ForkSlotRecovery(
                    wedged=True, recovered=False, killed_pids=killed,
                    detail=f"cannot kill hung module-fork child pid={pid}: {e}",
                )
            killed.append(pid)
            logger.warning(
                "#3845: killed hung redis-module-fork child pid=%s for %s — "
                "waiting for redis to reap it and release the fork slot",
                pid, socket_path_of(db),
            )

        remaining = find_hung_module_fork_children(db, min_age_s=0.0)
        if not remaining:
            if killed:
                return ForkSlotRecovery(
                    wedged=True, recovered=True, killed_pids=killed,
                    detail=(f"reaped {len(killed)} hung module-fork child(ren) "
                            f"{killed} — fork slot released"),
                )
            return ForkSlotRecovery(
                wedged=True, recovered=False,
                detail="no hung module-fork child of this daemon was found",
            )
        if time.monotonic() >= deadline:
            return ForkSlotRecovery(
                wedged=True, recovered=False, killed_pids=killed,
                detail=(f"hung module-fork child(ren) "
                        f"{[p for p, _ in remaining]} still held the slot "
                        f"after {timeout_s:.0f}s"),
            )
        time.sleep(poll_s)
