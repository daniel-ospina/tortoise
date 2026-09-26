"""Embedded redislite orphan reaper.

Epic #1647 P4 (Task 10) DEMOTION: this module is now DEV-MACHINE HYGIENE
ONLY. CI runs the docker lane — the fast matrix provisions falkordb, and
migrated files construct via the URI-aware redirect (never spawning a
redislite server); the carve-out files run embedded in the URI-unset
carve-out job, whose conftest `_redislite_hygiene` session sweeps own their
own orphan reclamation. Docker halves produce ~0 embedded orphans by
construction (E2E-7). The reaper keeps its local-dev role: a dev box's
embedded sessions can still strand servers on SIGKILL, and the scheduled
sweep (tools/install-reaper-schedule.sh) + the conftest sweeps reclaim
them. The orphan-COUNT assert is lane-aware (docker ~0 / carve-out <20,
set at Task 9 Step 4) — CI no longer depends on this module's correctness.

Finds orphaned redis-server processes spawned by redislite embedded mode and
classifies them for safe cleanup (issue #176, plan Child 1).

Classification (dual-signal):
  - socket NOT under tempfile.gettempdir()          -> protected (path-based)
  - registry has named db_filename                  -> protected (path-based)
  - old-format registry (no db_filename) + .db file -> protected
  - unknown old-format dirname pattern              -> protected (WARNING)
  - tempdir socket + uptime < MIN_UPTIME            -> protected (boot cooldown)
  - tempdir socket + uptime >= MIN_UPTIME + no db_filename + no .db -> candidate
  - path-based + registry-recorded owner pid DEAD   -> orphan (candidate; #1427)
  - path-based + LIVE server + 0 clients persisted  -> orphan (#1642 FIX 3:
    redislite's registry pidfile is the server's OWN pid, so the #1427 owner
    check is circular for live servers — orphanhood is decided from ppid=1
    detachment + CLIENT LIST zero-client state instead)
  - registry pidfile pid dead (Z-aware)           -> stale_socket (#1383)
    (dead-pid leftover dir — guarded-rmtree reaped, never a killable
    'candidate'; no CLIENT LIST probe happens — no server exists to list)

NEVER_KILL: anything classified protected (stable singleton, path-based
servers, boot-cooldown servers, unknown patterns). Only candidates may be
killed, and only after liveness + CLIENT LIST verification (see reap()).
Issue #1427: a path-based server whose registry-recorded owner pid is
provably dead is an orphan (the db file has no live owner), not live data
— it reclassifies as a candidate instead of staying protected forever
(the dominant leak class: aborted test servers). Live owners keep the
protection; unresolvable owners (no pidfile / unreadable) fail closed.
stale_socket records are NOT killed — they are removed via the guarded
rmtree action _remove_stale_socket_dir (containment -> pidfile re-read ->
ECONNREFUSED-only socket re-probe -> mtime age -> atomic rename-aside ->
post-rename re-probe -> rmtree of the renamed path only).

Probing is read-only and fail-closed: CLIENT LIST goes over a plain unix
socket (redis-cli or raw RESP) and never kills or mutates the probed
server (#849); if the client state cannot be determined the server is
skipped, never killed.

PROVENANCE GUARD (#4136, the T2/T3/T4 residual of #4098). `_is_ephemeral_dir`
is a SCOPE predicate (stay in our namespace) and never proved PROVENANCE:
on a shared world-writable tempdir (Linux `/tmp`, mode 1777) a different
local uid can author every piece of evidence the destruction predicates
read (the `tmp…` name, a dead socket, the mtime, the owner records, the
`redis.pid`). That made the reaper's SIGTERM and rmtree authority an
unprivileged attacker's: an attacker-authored `redis.config` `pidfile`
named a live victim pid (SIGTERM), and its `dir=` named a bystander dir
(rmtree). Every destruction path therefore now requires the candidate
directory to be OWNED BY THE INVOKING EFFECTIVE UID, re-checked at the
POINT OF ACTION (`_dir_owned_by_euid`, `O_NOFOLLOW`+`fstat`) so a path
swapped between discovery and the action is refused too (T4). Ownership is
the one property a foreign uid cannot forge. A kill whose candidate dir has
already vanished is authorized only by the live pid's OWN argv naming that
dir (the pass-1 binding — no foreign uid can edit another process's
command line). The policy is STRICT-ONLY:
there is deliberately no environment override, config flag, or allowlist
to act on another uid's directory — a root-run scheduled sweep therefore
reaps nothing (not the documented deployment; see
docs/infra/embedded-reaper-cron.md). Fail closed: when ownership cannot be
determined the destruction is skipped, never attempted.
"""
from __future__ import annotations

import errno
import json
import logging
import math
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

MIN_UPTIME_DEFAULT = 30
PROBE_TIMEOUT = 2.0  # seconds for raw socket probes
# Socket-level timeout for raw RESP probes. A live local server answers in
# milliseconds; 0.5s is generous and keeps sweeps fast at hundreds of
# unresponsive orphans (issue #1005 — the 2s timeout made serial sweeps
# take minutes).
PROBE_SOCKET_TIMEOUT = 0.5
# #1383: bounded retry before fail-closed skip — a single load-induced
# read/connect timeout must not strand a live orphan (indicator b). Only
# timeout-phase failures retry; refused/missing are reliable verdicts.
RAW_RESP_PROBE_ATTEMPTS = 2
# Boot-window shield for the stale-dir removal path (#1383): a leftover
# dir younger than this is never rmtree'd (the creating server may be
# mid-startup). Mirrors INDEX_PID_MIN_AGE_DEFAULT (30s) and the boot-
# cooldown philosophy; deliberately NOT coupled to TORTOISE_REAPER_MIN_UPTIME
# (live-server boot cooldown) — different semantics.
STALE_SOCKET_MIN_AGE_DEFAULT = 30
# Max stale removals per reap() call (#1383). Bounds the SERIAL stale work
# vs the 120s SIGALRM. Common case: dead sockets answer ECONNREFUSED
# instantly, so 200 removals cost <1s. Worst case (every probe hangs at
# 0.5s, 3 probes per stale) may exceed the SIGALRM — idempotent (next sweep
# converges); the budget is the backstop for the common case.
STALE_SWEEP_BUDGET = 200
# Rename-aside suffix marking a reaper-owned quarantine dir (plan-review
# P1: discover pass 2 and the stale action must both skip these — they are
# handled exclusively by _sweep_quarantine_dirs).
STALE_QUARANTINE_SUFFIX = ".reaper-stale-"
# #3599: owner records. tortoise.FalkorDB writes one file per owning process
# into the server's OWN socket dir (`<socket_dir>/.tortoise-owners/<pid>-<start>`),
# removed on every close seam. Consulted by _owner_records below so orphanhood
# is decided PER SERVER ("does this server still have a live owner?") instead
# of by the global suite-marker gate (#1642 FIX 4), which on a host running a
# fleet of concurrent sessions is permanently True and therefore never
# confirmed anything — the #3599 deadlock. The writer lives in
# tortoise/embedded_lifecycle.py and imports this name lazily (no import
# cycle: embedded_reaper deliberately stays dependency-free).
OWNERS_DIRNAME = ".tortoise-owners"
# #4926: the MID-CONSTRUCTION claim file inside `OWNERS_DIRNAME`. A client
# that has adopted this socket from the redislite registry but has not yet
# attached (it is blocked in `_wait_for_server_start`, so it holds no
# connection) writes `<OWNER_INFLIGHT_PREFIX><pid>-<start>-<socket-digest>`
# for the whole duration of `RedisMixin.__init__`. It is a LIVENESS-ONLY hold
# read by `embedded_lifecycle.cotenant_holds_server` (the last-client
# decision), so that a peer process's close cannot tear the server down under
# the attaching construction. The prefix is load-bearing: it is deliberately
# unparseable as a `<pid>` record name, so BOTH parsers in this module
# ignore these files entirely — `_owner_records` (the `total`/`live`
# arithmetic) and `_owner_record_dir_present` (which feeds
# `_has_ownership_claim` and the `unattributed` flag). A construction claim
# is not an owner record and must never move either. Declared here (the
# reaper owns the owner-record format) so writer and reader cannot drift.
OWNER_INFLIGHT_PREFIX = "inflight-"
# #4577: the owner-hold LOCK file inside `OWNERS_DIRNAME`. The writer holds a
# SHARED `flock(LOCK_SH)` on it for the process's lifetime; the reaper probes
# an EXCLUSIVE non-blocking lock. A held lock is a KERNEL FACT of liveness
# (the kernel drops it when the holder dies) and supersedes the pid+start
# inference as the PRIMARY signal — `_owner_records` is retained as the
# fallback for owners created before the lock existed and for lock-less
# paths. Declared here (the reaper owns the owner-record format) so the
# writer and reader cannot drift.
OWNER_LOCK_NAME = ".lock"
# #1383 security review (Issue 3): ownership marker written into a
# quarantine at rename time — the sweep only rmtrees dirs carrying it, so a
# same-suffix foreign dir (another tool's temp naming, a planted decoy) is
# never touched.
REAPER_OWNED_MARKER = ".reaper-owned"
# #4068: the marker filenames that identify a dir carrying a redislite
# server. Declared once so discovery and classification share one contract.
SOCKET_MARKER = "redis.socket"
PIDFILE_MARKER = "redis.pid"
# NOTE (#4068): deliberately a STRICTER predicate than EPHEMERAL_PREFIXES.
# This one is a name-SHAPE allowlist consumed by the classifier in the
# no-registry / old-format branches, where a `redislite_*`/`tmp*` dir
# OUTSIDE the tempdir must still read as auto-generated — the containment
# check (_is_ephemeral_dir) cannot reach such a dir. DISCOVERY must not use
# it: discovery is depth-1 and uses EPHEMERAL_PREFIXES, which is exactly
# the predicate every REMOVAL path requires there (kills are covered by the
# pass-1 lemma — see _ephemeral_name).
_AUTOGEN_DIRNAME = re.compile(r"^(redislite_|tmp)[a-zA-Z0-9_]+$")

# Ephemeral tmp-tree prefixes (under the system tempdir) that test code
# creates with tempfile.mkdtemp — servers rooted in these trees are
# disposable once their clients are gone (issue #1005). User-home dirs
# (tortoise-test-home-*, tortoise-lifecycle-*) are NOT under the tempdir,
# so the tempdir-containment check keeps them protected.
EPHEMERAL_PREFIXES = (
    "redislite_", "tmp", "pytest-of-", "tortoise_", "tortoise-",
    "tortoise_test_", "tortoise_shared_embedded_", "tortoise_m0_",
    "tortoise-hardreject-", "tortoise-concurrency-", "tt_", "pack_v3_bad_",
    # #1642 FIX 7: longmem_eval builds one isolated graph per question under
    # tempfile.TemporaryDirectory(dir=work_dir, prefix="lme-") — 58 such dirs
    # were observed protected on the dev box (unrecognized pattern -> the
    # `protected` fail-closed), so a SIGKILLed --workers 8 run leaked every
    # server. The lme- trees are disposable test trees like tt_/tortoise_*.
    "lme-",
)

# Marker dir for active pytest suites (conftest writes/removes one file per
# suite session; the reaper consults it so a sweep never kills a concurrent
# suite's between-tests idle server — issue #1005 P1).
ACTIVE_SUITES_DIR = os.path.join(
    os.path.realpath(tempfile.gettempdir()), ".tortoise", "active_suites")

# Default kill pacing (seconds between serial SIGTERMs) — synchronized
# shutdown bursts ARE the bgsave storm this module exists to prevent.
KILL_PACING_DEFAULT = 0.5
DEFAULT_BATCH_SIZE = 50

# #1642 FIX 3 (#1427): a live server is only "orphan-confirmed" when its
# 0-client CLIENT LIST state has persisted across sweeps for at least this
# long. The cron cadence (20 min) makes this natural: sweep 1 records the
# zero-client observation, a later sweep confirms. The wait distinguishes a
# genuine orphan from a concurrent suite's between-tests idle server, which
# also sits at 0 clients (#1557 — redislite servers all daemonize to ppid=1,
# so detachment alone cannot discriminate).
ZERO_CLIENT_CONFIRM_MINUTES = 10.0

# #1642 review P2: a suite marker older than this is provably a crash
# leftover (pytest sessions never run for days) — prune it so its recycled
# pid can never pin suites_active=True and disable live-orphan kills.
MARKER_MAX_AGE_S = 24 * 3600
# State entries older than this are pruned (a confirmed/reaped server leaves
# an entry; the socket dir is gone so it is never seen again).
ZERO_CLIENT_STATE_MAX_AGE = 7 * 86400.0
# Persisted zero-client observation state (pid + process start time, so a
# recycled pid restarts the confirmation window — #1642 FIX 5).
ZERO_CLIENT_STATE_PATH = os.path.join(
    os.path.expanduser("~"), ".tortoise", "reaper-zero-client.json")

# #1642 FIX 2 (#1449): time budget for the socket-dir walk. The walk is a
# backstop, never a gate on the tempdir's entry count (pollution disabled
# cleanup — chicken-and-egg).
# #4068: the walk is now an IN-PROCESS `os.scandir` depth-1 enumeration, so
# this is a monotonic DEADLINE (like reap()/_run_sweep) rather than a
# `find` subprocess timeout — and it is sampled every iteration, so expiry
# is always a loud WARNING + a partial result, never a silent [].
#
# OVERRIDES: #1642 FIX 2's unconditional full-tempdir walk — pass-2
# DISCOVERY is now name-scoped to the ephemeral namespace. Every REMOVAL
# path already required that predicate at depth 1, and live servers are
# enumerated name-independently by pass 1, so no removable/killable record
# is lost; the recorded #1642 intent (never gate on the tempdir's entry
# COUNT) still holds, as a name scope is a different class. Detection is
# restored by `--full-scan`. See issue #4068.
SOCKET_WALK_TIMEOUT = 20.0


def _is_ephemeral_dir(dbdir_real: str, tmpdir_real: str) -> bool:
    """True when dbdir sits under the system tempdir AND any path component
    BELOW the tempdir root starts with a known ephemeral test prefix
    (issue #1005).

    Uses strict relative_to containment (not string startswith — /tmp2 must
    not match a /tmp tempdir) and excludes the tempdir root itself: on Linux
    the tempdir IS /tmp, so the root's own 'tmp' component must never
    classify everything beneath it as ephemeral. Checks all components, not
    just the basename: pytest tmp trees nest servers as
    pytest-of-<user>/pytest-N/<test_name>/… where the dbdir basename is the
    test name. The containment check is the safety boundary: user-home test
    dirs (tortoise-test-home-*, tortoise-lifecycle-*) never match.
    """
    try:
        rel = Path(dbdir_real).relative_to(Path(tmpdir_real))
    except ValueError:
        return False
    return any(part.startswith(EPHEMERAL_PREFIXES) for part in rel.parts)


def _dir_owned_by_euid(path: str | None) -> bool:
    """PROVENANCE guard (#4136): True only when `path` is a directory owned
    by the invoking effective uid.

    This is the boundary `_is_ephemeral_dir` never was. The reaper's
    destruction predicates are all authorized by files read out of the
    candidate directory; on a shared, world-writable tempdir (Linux `/tmp`,
    mode 1777) a different local uid can author every one of them. Inode
    ownership is the one property a foreign uid cannot forge (DAC plus the
    sticky bit make the entry theirs, not ours), so it is what every
    destruction path requires.

    Evaluated at the POINT OF ACTION, never cached from discovery: the dir
    is re-opened here with `O_NOFOLLOW`/`O_DIRECTORY` and the ownership read
    from the resulting fd (`fstat`), so the verdict belongs to the inode the
    action is about to touch and a path swapped for a symlink in the
    discovery↔action window is refused (T4 of #4098's threat model).

    Fail closed: a missing path, a non-directory, an unopenable dir, or an
    unreadable owner returns False. No override exists by decision (#4136).
    """
    if not path:
        return False
    flags = (os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
             | getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(path, flags)
    except OSError:
        return False
    try:
        st = os.fstat(fd)
    except OSError:
        return False
    finally:
        os.close(fd)
    if not stat.S_ISDIR(st.st_mode):
        return False
    return st.st_uid == os.geteuid()


def _dir_owner_of(path: str | None) -> int | None:
    """Owning uid of `path` (no-follow), or None when unreadable — for
    log lines only; never an authorization decision (use
    `_dir_owned_by_euid`)."""
    if not path:
        return None
    try:
        return os.lstat(path).st_uid
    except OSError:
        return None


def _pid_cmdline_names_dir(pid: int, dbdir: str) -> bool:
    """True when the LIVE process `pid`'s own argv names `dbdir`.

    #4136: the unforgeable provenance binding for the socket-less kill arm.
    A foreign uid cannot edit another process's command line, so "this live
    pid's argv names this directory" cannot be authored by reading files out
    of the candidate dir. Two forms are matched:
      - `_socket_dir_from_cmdline` (the pass-1 binding: an inline
        `unixsocket:`/`--unixsocket` argv, or the config file's own directive);
      - the config-file argv form redislite uses on macOS
        (`redis-server <dbdir>/redis.config …`) — matched on the ARGV path
        alone, because the config file itself is often already gone with the
        directory, which would otherwise strand a genuine socket-less orphan.

    `dbdir` is compared as ALREADY-CANONICAL text (it is `os.path.realpath`'d
    at discovery, as is `_socket_dir_from_cmdline`'s result) and is NEVER
    re-resolved here: `os.path.realpath(dbdir)` would follow a symlink the
    attacker can plant at the very path this arm has just observed absent,
    forging the binding (the caller's T0-absent race). Only the argv-derived
    side is resolved, and that side is the victim's own argv.
    """
    if not pid or not dbdir:
        return False
    try:
        named = _socket_dir_from_cmdline(pid)
        if named and named == dbdir:
            return True
        m = re.search(r"(\S+/redis\.config)\b", _cmdline(pid))
        if not m:
            return False
        argv_dir = os.path.realpath(os.path.dirname(m.group(1)))
    except OSError:
        # A path that vanishes/loops mid-resolution (an attacker toggling it)
        # must fail closed, never abort the sweep.
        return False
    return argv_dir == dbdir


def _kill_provenance_refusal(record: dict) -> str | None:
    """Return a refusal reason when a kill is not provenance-authorized.

    #4136: a SIGTERM is authorized EITHER by the candidate directory (the
    home of every piece of evidence that admitted the record) being owned
    by our euid, OR — when that directory is gone — by the live pid's OWN
    argv naming that directory (`_pid_cmdline_names_dir`, the pass-1
    binding).

    The second arm preserves the legitimate socket-less orphan class
    (#1642 FIX 3): such a record can only ever be discovered from the live
    server's own command line, so the binding cannot be forged by a foreign
    uid — it cannot edit another process's argv — while a decoy whose dir is
    deleted in the discovery↔action window (T4) is refused.

    Presence is a SINGLE `lstat` snapshot and the absent arm never resolves
    `dbdir`, so an attacker who toggles the path (absent → symlink to a dir
    the victim's argv names) cannot forge the binding.

    None means "authorized". Fail closed: an unreadable/absent dir with no
    pid binding is refused.
    """
    dbdir = record.get("dbdir") or ""
    if not dbdir:
        socket_path = record.get("socket_path") or ""
        dbdir = os.path.dirname(socket_path) if socket_path else ""
    if not dbdir:
        return "candidate carries no directory to authorize its kill"
    try:
        os.lstat(dbdir)
        present = True
    except FileNotFoundError:
        present = False
    except OSError:
        # unreadable / ELOOP / etc — cannot prove provenance, fail closed
        return f"candidate dir {dbdir!r} cannot be inspected"
    if present:
        if _dir_owned_by_euid(dbdir):
            return None
        return (f"candidate dir {dbdir!r} is not owned by euid "
                f"{os.geteuid()} (owner {_dir_owner_of(dbdir)})")
    pid = record.get("pid")
    if pid and _pid_cmdline_names_dir(pid, dbdir):
        return None
    return (f"candidate dir {dbdir!r} is gone and no live process names it "
            f"(pid {pid})")


def _has_ownership_claim(dbdir_real: str,
                         pid: int | None) -> str | None:
    """#3767: the POSITIVE ownership claim that makes a registry-less
    directory OURS to reap. Returns None when the claim holds, else the
    refusal reason.

    `dbdir_real` is the candidate's SOCKET directory (`_classify`'s
    `dbdir_real`) — the same dir `record_owner` writes the
    `.tortoise-owners` instrument into.

    Containment ("under the shared tempdir") and a `tmpXXXX`/`redislite_*`
    NAME are evidence any co-resident same-uid redislite application also
    produces, so neither is a claim: a directory's name is not ownership.
    Two arms are admissible, both positive:

      (a) PRESENT directory — it must be owned by the invoking effective uid
          (the one property a foreign uid cannot forge; #4136's guard,
          applied at ADMISSION here rather than only at destruction) AND
          carry tortoise's own per-server instrument, the
          `.tortoise-owners` record directory written by `tortoise.FalkorDB`
          (#3599). A registry-less redislite server with neither is
          indistinguishable from another application's, so it is not a
          candidate. (The redislite registry itself is written by redislite,
          not tortoise — when it is ABSENT there is no claim to read, which
          is exactly this branch.)

      (b) ABSENT directory — the #1005 / #1642 FIX 3 socket-less residue
          class: the LIVE process's own argv names the (now-absent)
          directory. Unforgeable by reading files out of the candidate dir
          (#4136's pass-1 binding), which is why it is admissible without a
          per-directory instrument. The #1557 confirmation window still
          gates the kill — a live server whose dir was unlinked keeps
          serving established connections, so a missing dir is never instant
          proof of orphanhood.

    The euid arm is dir-present only: a missing path fails
    `_dir_owned_by_euid` by construction, and arm (b) carries the argv
    binding instead. Fail closed: a present dir whose ownership or
    instrument cannot be established, or an absent dir with no live pid
    naming it, is refused.

    RECONCILIATION with #4577's held-lock LIVENESS signal (main `845f47f53`):
    `_owner_lock_held()` is deliberately NOT consulted here. #4577 answers
    "is an owner ALIVE?" (a kernel fact); this function answers "is this dir
    OURS to reap?" (attribution) — a held lock is a liveness proof, not an
    admission claim. The decisive reason is outcome-based: admission is
    PERMISSIVE in effect (it makes a dir a KILL candidate), and a dir admitted
    on a lock arm would be vetoed by `reap()`'s
    ``_owner_lock_held(...) is True`` check — a held lock is exactly what
    makes `reap()` skip — so the arm could never produce a kill; it would only
    widen #3767's gate for zero reaping gain (against the #4546 requirement
    that the gate stay narrow).

    So the lock is authorised where it belongs — at DESTRUCTION, not at
    admission — and `_owner_record_dir_present` stays consistent with it by
    ignoring dotted names, so the new `.lock` file cannot satisfy this claim
    (pinned by `tests/test_reaper_ownership.py::
    test_owner_lock_file_alone_is_not_an_ownership_claim`).

    CAVEAT on the adjacent claim: the writer's ordering (`record_owner` takes
    the record before the lock; `forget_owner` releases the lock before
    unlinking the record) does NOT make "held lock, no recognisable record"
    unreachable — the stamp ``<pid>-<start>`` carries no socket identity, so
    one process owning two sockets in one directory shares a record and the
    first `forget_owner` can unlink it while the second socket's flock is
    still held. That state is refused here, and it must be: a live lock holder
    must never be killed.

    NOTE (#3767): this decides ADMISSION only; it does NOT replace #4136's
    action-time `_dir_owned_by_euid` re-checks (`_kill_provenance_refusal`,
    `_cleanup_tempdir`, `_remove_stale_socket_dir` guard 5.5). A
    discovery-time verdict is cached and therefore T4-unsafe on its own.
    """
    if os.path.isdir(dbdir_real):
        if not _dir_owned_by_euid(dbdir_real):
            return (f"dir not owned by euid {os.geteuid()} "
                    f"(owner {_dir_owner_of(dbdir_real)})")
        if not _owner_record_dir_present(dbdir_real):
            return (f"no tortoise ownership record ({OWNERS_DIRNAME}) in "
                    f"{dbdir_real}")
        return None
    if pid and _pid_cmdline_names_dir(pid, dbdir_real):
        return None
    return f"dir {dbdir_real!r} is absent and no live process names it"


def active_suite_tokens() -> list[str]:
    """List active pytest-suite marker tokens (filenames in ACTIVE_SUITES_DIR).

    Each marker is created by a suite's conftest at session start and removed
    at session end. Stale markers (pid dead, or (pid, start_time) mismatch —
    a recycled pid, #1642 FIX 5) are treated as absent so one crash cannot
    permanently degrade later suites' sweeps to only-safe (issue #1005
    review P2). Empty when no other suite is mid-run.
    """
    return [m["token"] for m in active_suite_markers()]


def active_suite_markers() -> list[dict]:
    """Liveness-verified active-suite marker records.

    Returns [{token, pid, start}] for markers whose recorded (pid,
    start_time) identity is live (#1642 FIX 5: a recycled pid — live but a
    DIFFERENT process — counts as stale, so a SIGKILLed suite's marker can
    never defer later sweeps forever). Markers without a parsable pid or
    start are skipped (fail toward absent).
    """
    try:
        entries = os.listdir(ACTIVE_SUITES_DIR)
    except OSError:
        return []
    now = time.time()
    markers = []
    for e in entries:
        if e.startswith("."):
            continue
        mpath = Path(ACTIVE_SUITES_DIR, e)
        try:
            # #1642 review P2: an age-guard — pytest sessions never run for
            # days, so a marker older than 24h is provably a crash leftover
            # (its pid may have been recycled to ANY live process, which the
            # pid-only legacy-format markers can't detect). Prune it
            # opportunistically so it can never pin suites_active=True and
            # silently disable live-orphan kills (the #1642 recurrence).
            if now - mpath.stat().st_mtime > MARKER_MAX_AGE_S:
                try:  # noqa: SIM105
                    mpath.unlink()
                except OSError:
                    pass
                continue
            text = mpath.read_text()
        except OSError:
            continue
        if not text.strip():
            continue  # empty/partial marker (failed write) -> stale
        m = re.search(r"pid=(\d+)", text)
        if not m:
            continue  # no parsable pid -> stale
        try:
            pid = int(m.group(1))
        except (ValueError, OverflowError):
            continue  # malformed pid -> stale, never fail the sweep
        sm = re.search(r"start=([\d.]+)", text)
        start = None
        if sm:
            try:
                start = float(sm.group(1))
            except ValueError:
                start = None
        if not _pid_identity_matches(pid, start):
            continue  # dead pid OR recycled (start mismatch) -> stale
        markers.append({"token": e, "pid": pid, "start": start})
    return markers


def _dir_missing_on_disk(dbdir: str | None) -> bool:
    """True when the registry's DB dir no longer exists (pytest cleaned it at
    session end, or the creating suite is gone). A server whose data dir is
    gone cannot serve anyone — safe to reap regardless of concurrency.
    """
    return bool(dbdir) and not os.path.exists(dbdir)


def _parse_min_uptime() -> int:
    """Parse TORTOISE_REAPER_MIN_UPTIME (float-safe, shared by CLI + discover).

    - float strings (e.g. "30.5") parsed as float then truncated
    - negative -> 0 (with warning)
    - non-numeric / empty -> default 30 (with warning)
    - huge (> 3600) -> accepted with warning
    """
    raw = os.environ.get("TORTOISE_REAPER_MIN_UPTIME", "")
    if raw == "":
        return MIN_UPTIME_DEFAULT
    try:
        val = int(float(raw))  # float-safe: "30.5" -> 30
    except (ValueError, TypeError):
        logger.warning(
            "TORTOISE_REAPER_MIN_UPTIME=%r not numeric — using default %s",
            raw, MIN_UPTIME_DEFAULT)
        return MIN_UPTIME_DEFAULT
    if val < 0:
        logger.warning(
            "TORTOISE_REAPER_MIN_UPTIME=%r negative — treating as 0", raw)
        return 0
    if val > 3600:
        logger.warning(
            "TORTOISE_REAPER_MIN_UPTIME=%r > 3600 — unusually large", raw)
    return val


#: #4214: `os.path.realpath(tempfile.gettempdir())` memoized once per process.
#: The reaper calls this from its per-record classification loop, and the
#: realpath walk re-stats the temp root itself — on a leak-degraded box that
#: is the single most expensive syscall in the sweep (0.4–3.4 s measured at
#: nlink 55 k). The value is a process constant, BUT `tempfile.tempdir` is
#: assignable (tests redirect it), so the memo is keyed on the raw value and
#: is recomputed whenever that changes — a pure memo with no invalidation
#: would pin the first root forever and silently defeat every redirect.
_REAL_TEMPDIR: str | None = None
_REAL_TEMPDIR_RAW: str | None = None


def _real_gettempdir() -> str:
    global _REAL_TEMPDIR, _REAL_TEMPDIR_RAW
    raw = tempfile.gettempdir()
    if _REAL_TEMPDIR is None or raw != _REAL_TEMPDIR_RAW:
        _REAL_TEMPDIR = os.path.realpath(raw)
        _REAL_TEMPDIR_RAW = raw
    return _REAL_TEMPDIR


def _registry_for(socket_dir: str) -> dict | None:
    """Read the redislite registry for a socket dir.

    redislite writes the `.settings` JSON registry at the DB dir (user path
    for path-based servers — NOT in the socket tempdir). The socket tempdir
    always contains `redis.config` with `dbfilename` + `dir`, which is the
    authoritative discriminator. Prefer redis.config (always present next
    to the socket); fall back to a *.settings file if present.

    Returns dict or None. Never raises — per-file error isolation.
    """
    config = _read_redis_config(socket_dir)
    if config is not None:
        return config
    for p in Path(socket_dir).glob("*.settings"):
        try:
            return json.loads(p.read_text())
        except (json.JSONDecodeError, OSError, PermissionError, UnicodeDecodeError):
            logger.warning("corrupt settings file skipped: %s", p)
            return None
    return None


def _read_redis_config(socket_dir: str) -> dict | None:
    """Parse redis.config (next to the socket) for dbfilename + dir.

    redis.config lines: `dbfilename 'redis.db'` / `dir '/path'` /
    `unixsocket '...'` / `pidfile '...'`.
    """
    cfg_path = os.path.join(socket_dir, "redis.config")
    if not os.path.exists(cfg_path):
        return None
    try:
        content = Path(cfg_path).read_text()
    except (OSError, PermissionError, UnicodeDecodeError):
        logger.warning("unreadable redis.config skipped: %s", cfg_path)
        return None
    result = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        key, val = parts[0], parts[1].strip().strip("'\"")
        result[key] = val
    if not result:
        return None
    return result


def _stdout_text(out) -> str | None:
    """The captured stdout of a completed subprocess as ``str``, else None.

    Every probe in this module is documented to fail CLOSED — ``None`` or an
    empty result meaning "undeterminable" — and each one parses captured
    ``ps``/``lsof``/``pgrep``/``redis-cli`` stdout with ``.strip()``,
    ``.splitlines()`` or ``re.match``. A subprocess seam that hands back
    anything other than text therefore used to raise ``TypeError`` from a
    helper whose contract is "or None". The common real-world source is a
    monkeypatched ``subprocess.run`` (dozens of tests fake git detection that
    way), but any wrapper is enough — so the type guard belongs here, at the
    read, not at each of the callers.

    ``bytes`` is decoded; anything else (including a missing attribute, since
    the value is genuinely untyped) is ``None``.
    """
    raw = getattr(out, "stdout", None)
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", "replace")
        except Exception:  # undecodable is undeterminable — fail closed
            return None
    return raw if isinstance(raw, str) else None


def _run_text(cmd: list[str], *, timeout: float,
              env: dict[str, str] | None = None) -> str | None:
    """Run ``cmd`` and return its stdout as text, or None when unusable.

    Thin wrapper over ``subprocess.run(capture_output=True, text=True)`` that
    swallows the timeout/OS failures the callers already handled AND applies
    :func:`_stdout_text`, so a non-text stdout is "undeterminable" rather
    than a ``TypeError`` escaping a fail-closed probe.

    Why this is load-bearing rather than defensive tidiness (#4496): the
    raise propagated out of ``_owner_records`` into
    ``cotenant_holds_server``, whose deliberate fail-closed
    ``except Exception: return True`` then reported a PHANTOM co-tenant. The
    last of two clients therefore declined the shutdown and its embedded
    daemon leaked — uninstrumented, directory present: exactly the
    ``candidate / path_based=False / unattributed=True / dir_missing=False``
    shape `test-slow (b)` reports, and the class #3767 refuses to fast-kill.
    """
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, env=env)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return _stdout_text(out)


def _uptime_seconds(pid: int) -> float | None:
    """Return process uptime in seconds, or None if the PID is not alive.

    macOS: ps -o etime gives [[dd-]hh:]mm:ss; Linux /proc/<pid>/stat is
    preferred but ps -o etime works on both. Consults the per-sweep
    _PROC_INFO_CACHE when available (issue #1005 — one batched ps call
    instead of one subprocess spawn per server).
    """
    cached = _PROC_INFO_CACHE.get(pid)
    if cached is not None:
        return cached["etime"]
    etime = _run_text(["ps", "-o", "etime=", "-p", str(pid)], timeout=2)
    if etime is None:
        return None
    etime = etime.strip()
    if not etime:
        return None
    return _parse_etime(etime)


def _parse_etime(etime: str) -> float:
    """Parse ps etime '[[dd-]hh:]mm:ss' into seconds."""
    etime = etime.strip()
    days = 0
    if "-" in etime:
        days_part, etime = etime.split("-", 1)
        days = int(days_part)
    parts = [int(x) for x in etime.split(":")]
    if len(parts) == 3:  # hh:mm:ss
        hours, minutes, seconds = parts
    elif len(parts) == 2:  # mm:ss
        hours, minutes, seconds = 0, parts[0], parts[1]
    else:
        return 0.0
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OverflowError):
        return False
    except OSError:
        return False
    # #1383: a zombie answers kill(0) but its fds are gone — treat as dead
    # (precedent: test_embedded_concurrency._pid_alive, #1365). Linux-only:
    # macOS has no /proc; plain kill(0) behavior there is documented.
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        # stat = "pid (comm) state ..." — comm may contain spaces, so the
        # state field is the first token AFTER the closing paren.
        state = stat_text[stat_text.rfind(")") + 2:].split()[0]
        if state == "Z":
            return False
    except (OSError, IndexError, ValueError):
        pass  # macOS: no /proc — fall back to kill(0) semantics
    return True


# ── #1642 FIX 5 (#1448): (pid, process-start-time) identity ─────────
# A recycled pid (a live NON-redis process now holding the number) defeats
# plain kill(0) liveness. Store/verify the process start time alongside the
# pid wherever the reaper persists process identity (active-suite markers,
# zero-client state), and treat an alive-but-not-redis pid read from a
# redis.pid as recycled (provably not the recorded server).

_LSTART_RE = re.compile(
    r"^\S+\s+(?P<a>\S+)\s+(?P<b>\S+)\s+(?P<c>\S+)\s+(?P<y>\d+)$")
_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun",
           "jul", "aug", "sep", "oct", "nov", "dec"}


def _parse_lstart(raw: str) -> float | None:
    """Parse `ps -o lstart=` into epoch seconds (portable).

    macOS emits `Sun 23 Aug 23:03:24 2026` (day before month); Linux emits
    `Wed Aug 23 10:00:00 2026` (month before day). Tolerates a space-padded
    single-digit day. Returns None on any parse failure.
    """
    m = _LSTART_RE.match(raw.strip())
    if not m:
        return None
    a, b, c, y = m.group("a"), m.group("b"), m.group("c"), m.group("y")
    if b[:3].lower() in _MONTHS:
        mon, day = b, a
    elif a[:3].lower() in _MONTHS:
        mon, day = a, b
    else:
        return None
    try:
        day = day.strip().zfill(2)
        return time.mktime(time.strptime(
            f"{mon} {day} {c} {y}", "%b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def _process_start_time(pid: int) -> float | None:
    """Epoch-seconds start time of a process, or None when undeterminable.

    Consults the per-sweep _PROC_INFO_CACHE when available (one batched ps
    call in discover()/#mark_orphan_confirmation); falls back to a single
    `ps -o lstart=` subprocess. A recycled pid has a different start time,
    so (pid, start) verification defeats pid-reuse (#1642 FIX 5).
    """
    cached = _PROC_INFO_CACHE.get(pid)
    if cached is not None and cached.get("start") is not None:
        return cached["start"]
    raw = _run_text(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        timeout=2, env={**os.environ, "LC_ALL": "C"},
    )
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return _parse_lstart(raw)


def _pid_is_redis(pid: int) -> bool:
    """True when the process's cmdline identifies a redis-server.

    Used to detect recycled pids: the recorded pid in a redis.pid is the
    server's OWN pid (#1642 FIX 3), so a LIVE pid that is NOT a redis-server
    is provably a recycled number, not the recorded server. Cached cmdline
    when available; a vanished pid yields "" (False).
    """
    return "redis-server" in _cmdline(pid)


def _pid_effectively_alive(pid: int | None) -> bool:
    """Liveness of a pid READ FROM A redis.pid-style file (the server's own
    pid). Alive only when kill(0) passes AND the process is a redis-server:
    an alive non-redis pid is a recycled number and is treated as dead
    (#1642 FIX 5) — the recorded server is gone, so the leftover is reapable
    (the guarded stale path still re-verifies the socket before removal).
    """
    if not pid or not _pid_alive(pid):
        return False
    return _pid_is_redis(pid)


def _pid_identity_matches(pid: int, start: float | None,
                          tolerance: float = 2.0) -> bool:
    """(pid, start_time) identity check: pid alive AND the recorded start
    matches the current process start (within tolerance) — or no start was
    recorded (legacy record: pid-only verification). Recycled pids fail the
    start comparison (#1642 FIX 5)."""
    if not pid or not _pid_alive(pid):
        return False
    if start is None:
        return True  # legacy record without start — pid liveness only
    current = _process_start_time(pid)
    if current is None:
        return False  # cannot verify -> fail closed
    return abs(current - start) < tolerance



def _registry_owner_alive(registry: dict | None) -> bool | None:
    """Liveness of the registry-recorded owning process (issue #1427).

    The registry records a pidfile path; the pid file's content is the
    server's owning process pid. Returns True when that pid is alive,
    False when provably dead (orphan), None when unresolvable (no
    pidfile, pid file missing/unreadable, unparseable content). Callers
    must fail closed on None — an unknown owner keeps protection.

    #1642 FIX 3 (#1427 circularity): redislite's pidfile is the server's
    OWN pid (redis.pid), so this is True for every live server — it cannot
    distinguish an orphan from a live one. _classify therefore routes LIVE
    servers through the detachment + persisted-0-client orphanhood decision
    instead of this check; this remains the dead-owner reclassification
    (a provably dead — or, per FIX 5, recycled — pid is an orphan leftover).
    """
    if not registry or not registry.get("pidfile"):
        return None
    try:
        pid = int(Path(registry["pidfile"]).read_text().strip())
    except (OSError, ValueError, TypeError):
        return None
    if _pid_effectively_alive(pid):  # noqa: SIM103
        return True
    return False


def _is_detached(pid: int) -> bool:
    """True when the process's direct parent is init (pid 0/1).

    NOTE (#1557): redislite servers ALWAYS daemonize to ppid=1, so this is
    True for every redislite server — it does NOT indicate an orphan.
    Consulted only in FULL sweeps (only_safe=False); the only_safe path
    never kills live-pid servers regardless of detachment. Retained for the
    full-sweep admission logic (a reparented server with a live pid and an
    intact dir is the strongest orphan signal the full sweep has).

    Fail-closed: any uncertainty (ps timeout/error, pid vanished, unparseable
    ppid) returns False so the server is protected, never risked.
    """
    cached = _PROC_INFO_CACHE.get(pid)
    if cached is not None and cached.get("ppid") is not None:
        return cached["ppid"] in (0, 1)
    raw = _run_text(["ps", "-o", "ppid=", "-p", str(pid)], timeout=5)
    if raw is None:
        return False
    raw = raw.strip()
    if not raw:
        return False  # pid vanished mid-check -> reap() skips dead pids anyway
    try:
        ppid = int(raw)
    except ValueError:
        return False
    return ppid in (0, 1)


def _derive_real_pid(socket_path: str, pidfile_pid: int | None = None) -> int | None:
    """Derive the real redis-server PID for a live socket.

    Strategy:
      1. If pidfile_pid is alive AND lsof confirms it owns the socket ->
         pidfile_pid is authoritative (normal case).
      2. Otherwise scan redis-server processes via lsof and check which one
         has the socket path in its cmdline (respawned case).
    Returns None if undeterminable (caller treats as undetermined).
    """
    if pidfile_pid and _pid_alive(pidfile_pid) and _process_has_socket(pidfile_pid, socket_path):
        return pidfile_pid
    # Scan redis-server processes for the socket owner
    if sys_platform() == "linux":
        return _derive_real_pid_linux(socket_path)
    return _derive_real_pid_macos(socket_path)


def _process_has_socket(pid: int, socket_path: str) -> bool:
    """True if the process has the given unix socket open (lsof)."""
    text = _run_text(
        ["lsof", "-Fp", "-a", "-p", str(pid), "-U"], timeout=2)
    if text is None:
        return False
    # -Fp prints 'p<pid>' entries; presence means it has unix sockets open.
    # Additionally verify cmdline contains the socket path for certainty.
    return f"p{pid}" in text


def sys_platform() -> str:
    import sys
    return sys.platform


def _derive_real_pid_linux(socket_path: str) -> int | None:
    try:
        ino = os.stat(socket_path).st_ino
    except OSError:
        return None
    for fd in Path("/proc").glob("*/fd/*"):
        try:
            if os.stat(fd).st_ino == ino:
                pid = int(fd.parts[2])
                if _pid_alive(pid):
                    return pid
        except (OSError, ValueError):
            continue
    return None


def _derive_real_pid_macos(socket_path: str) -> int | None:
    """Find the redis-server PID owning a socket via lsof + cmdline check.

    lsof -c redis-server lists all redis-servers; we pick the one whose
    cmdline contains our target socket path.
    """
    try:
        out = subprocess.run(
            ["lsof", "-Fp", "-c", "redis-server"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    text = _stdout_text(out)
    if text is None:
        return None
    for line in text.splitlines():
        if not line.startswith("p"):
            continue
        try:
            pid = int(line[1:])
        except ValueError:
            continue
        if not _pid_alive(pid):
            continue
        if socket_path in _cmdline(pid):
            return pid
    return None


def _cmdline(pid: int) -> str:
    cached = _PROC_INFO_CACHE.get(pid)
    if cached is not None:
        return cached["cmdline"]
    text = _run_text(
        ["ps", "-ww", "-o", "command=", "-p", str(pid)], timeout=2)
    return text if text is not None else ""


def _probe_socket(socket_path: str, timeout: float = PROBE_TIMEOUT) -> str:
    """Raw unix-socket connect probe (never redis-py — can't spawn).

    Four verdicts (#1383 — FileNotFoundError must NOT collapse into 'dead':
    a missing socket file is the mid-startup window and must fail closed):
      - 'dead'         (ECONNREFUSED — socket FILE EXISTS, no listener)
      - 'missing'      (FileNotFoundError — no socket file at all)
      - 'alive'        (accepts connections)
      - 'undetermined' (timeout / other error)
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(socket_path)
            return "alive"
        except ConnectionRefusedError:
            return "dead"
        except FileNotFoundError:
            return "missing"
        except socket.timeout:  # noqa: UP041
            return "undetermined"
        except OSError:
            return "undetermined"
        finally:
            s.close()
    except OSError:
        return "undetermined"


def _probe_socket_any(socket_path: str,
                      timeout: float = PROBE_SOCKET_TIMEOUT) -> str:
    """Probe a socket path that may exceed the macOS AF_UNIX sun_path limit
    (~104 bytes): the quarantine suffix can push a deep dir over it, making
    connect() fail ENAMETOOLONG even for a LIVE server. Probes through a
    SHORT symlink to the same inode (a server holding the socket accepts
    through any path to that inode) when the path is long; direct probe
    otherwise. Fail closed ('undetermined') if the symlink cannot be made.
    """
    path = os.path.abspath(socket_path)
    if len(path.encode("utf-8", "surrogateescape")) <= 100:
        return _probe_socket(path, timeout=timeout)
    link = os.path.join(_real_gettempdir(),
                        f".rp_{os.getpid()}_{time.time_ns()}.sock")
    try:
        os.symlink(socket_path, link)
    except OSError:
        return "undetermined"  # cannot verify — fail closed
    try:
        return _probe_socket(link, timeout=timeout)
    finally:
        try:  # noqa: SIM105
            os.unlink(link)
        except OSError:
            pass


def _client_list(socket_path: str) -> list[dict] | None:
    """CLIENT LIST over the unix socket — read-only, never kills the server.

    Raw RESP in-process first (issue #1005 perf: spawning a redis-cli
    subprocess per probed server costs ~1s each at hundreds of orphans);
    falls back to redis-cli when the raw probe fails. NEVER constructs a
    redislite client here: its close() terminates the orphan server being
    probed (issue #849 — fail-open kill).

    Returns parsed client dicts, or None if the probe failed (no listener,
    timeout, malformed reply). None means "client state unknown" — callers
    MUST fail closed (skip reaping). [] means the server reported zero
    clients.
    """
    # Fast path: raw RESP over a plain unix socket (no subprocess spawn).
    raw = _raw_resp_client_list(socket_path)
    if raw is not None:
        return raw
    # Fallback: redis-cli (still no redislite client — see docstring).
    try:
        out = subprocess.run(
            ["redis-cli", "-s", socket_path, "CLIENT", "LIST"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
        if out.returncode == 0:
            # rc 0 with empty stdout = zero clients (empty bulk string) —
            # a valid verdict, don't double-probe via the raw fallback.
            # `_stdout_text` keeps a non-text stdout OUT of the parse: rc 0
            # is redis-cli's own success word, so a mocked/odd stdout must
            # read as "0 clients" only when it really was empty text.
            text = _stdout_text(out)
            if text is not None:
                return _parse_client_list(text)
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _raw_resp_client_list(socket_path: str) -> list[dict] | None:
    """CLIENT LIST via raw RESP over a plain unix socket.

    Non-destructive by construction: a plain socket connect/send/close never
    mutates or terminates the probed server (issue #849: the previous
    fallback built a redislite FalkorDB client whose close() KILLED the
    orphan it was probing — fail-open data loss on the no-redis-cli path).

    #1383 bounded retry: RAW_RESP_PROBE_ATTEMPTS attempts of
    PROBE_SOCKET_TIMEOUT each, but only when the attempt failed on a
    socket.timeout in the READ or CONNECT phase (a loaded single-threaded
    server queuing CLIENT LIST / filling its backlog — both are
    load-sensitive, both retryable). Reliable verdicts (ECONNREFUSED /
    missing / other errors) never retry. Exhausted -> None -> callers fail
    closed unchanged.
    """
    for _attempt in range(RAW_RESP_PROBE_ATTEMPTS):
        clients, status = _raw_resp_probe_once(socket_path)
        if status != "timeout":  # ok, refused, missing, error
            return clients
        # timeout-phase failure: retry (bounded)
    return None


def _raw_resp_probe_once(socket_path: str) -> tuple[list[dict] | None, str]:
    """One probe attempt; returns (parsed clients or None, status).

    status ∈ {ok, refused, missing, timeout, error} — 'timeout' (read OR
    connect phase) is the only retryable outcome (socket.timeout ⊂ OSError
    on 3.10+, so it must be caught BEFORE the generic OSError handler).
    The socket is ALWAYS closed (outer finally — the connect-phase early
    returns must not leak FDs; plan-review cycle 2 P1).
    """
    s = None
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(PROBE_SOCKET_TIMEOUT)
        try:
            s.connect(socket_path)
        except ConnectionRefusedError:
            return None, "refused"
        except FileNotFoundError:
            return None, "missing"
        except socket.timeout:  # noqa: UP041
            return None, "timeout"  # connect-phase timeout — retryable too
        except OSError:
            return None, "error"
        try:
            s.sendall(b"*2\r\n$6\r\nCLIENT\r\n$4\r\nLIST\r\n")
            raw = _read_resp_reply(s)
        except socket.timeout:  # noqa: UP041
            return None, "timeout"  # read-phase timeout — the retry target
        except OSError:
            return None, "error"
    finally:
        if s is not None:
            try:  # noqa: SIM105
                s.close()
            except OSError:
                pass
    if raw is None:
        return None, "error"  # malformed/truncated reply — not timing
    return _parse_client_list(raw), "ok"


def _read_resp_reply(sock: socket.socket) -> str | None:
    """Read one RESP reply; return the bulk-string payload (CLIENT LIST).

    Returns None for non-bulk replies (-ERR, +OK, integers) or on
    truncation/malformed input — callers fail closed on None.
    """
    buf = b""
    while b"\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        if len(buf) + len(chunk) > 1 << 20:  # 1 MiB sanity cap
            return None
        buf += chunk
    head, rest = buf.split(b"\r\n", 1)
    if not head.startswith(b"$"):
        return None
    try:
        length = int(head[1:])
    except ValueError:
        return None
    if length < 0 or length > 1 << 20:
        return None
    while len(rest) < length + 2:
        chunk = sock.recv(4096)
        if not chunk:
            return None
        rest += chunk
    return rest[:length].decode("utf-8", errors="replace")


def _parse_client_list(raw: str) -> list[dict]:
    clients = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        entry = {}
        for kv in line.split():
            if "=" in kv:
                k, v = kv.split("=", 1)
                entry[k] = v
        clients.append(entry)
    return clients


def _active_client_count(socket_path: str) -> int | None:
    """Count non-reaper clients (SKIPME semantics).

    Our probing connection is the freshly-created one with age ~0 and
    idle ~0. Any connection with age >= 2s is a pre-existing real client.
    Named clients are also real users regardless of age.

    Returns None when the probe failed — the caller must fail closed
    (a server whose client state is unknown must never be reaped).
    """
    clients = _client_list(socket_path)
    if clients is None:
        return None
    count = 0
    for c in clients:
        if c.get("name"):
            count += 1
            continue
        try:
            age = int(c.get("age", 0))
        except ValueError:
            age = 0
        if age >= 2:
            count += 1
    return count


_UNIXSOCKET_RE = re.compile(r"unixsocket:(\S+)")
# Linux daemonized redis re-execs with its effective config as long-form
# argv (`--unixsocket /path`); macOS uses the colon form above. Both must
# parse or the live pass silently misses live orphans on one platform.
_UNIXSOCKET_LONG_RE = re.compile(r"--unixsocket\s+(\S+)")

# Per-sweep process-info cache (issue #1005): populated with ONE batched ps
# call in discover(), consulted by _cmdline/_uptime_seconds so classifying
# hundreds of servers costs one subprocess spawn, not hundreds.
_PROC_INFO_CACHE: dict[int, dict] = {}


def _batch_process_info(pids: list[int]) -> dict[int, dict]:
    """One ps call for all pids: {pid: {cmdline, etime, ppid, start}}."""
    if not pids:
        return {}
    text = _run_text(
        ["ps", "-ww", "-o", "pid=,etime=,ppid=,lstart=,command=",
         "-p", ",".join(str(p) for p in pids)],
        timeout=10, env={**os.environ, "LC_ALL": "C"},
    )
    if text is None:
        return {}
    result: dict[int, dict] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 8)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        # lstart = 6 whitespace-separated tokens on both platforms
        # (macOS: `Mon 24 Aug 02:17:56 2026`, Linux: `Wed Aug 23 10:00:00
        # 2026`), so the full command starts at parts[8] (split(None, 8)
        # keeps the cmdline intact in parts[8]; parts[7] is the lstart YEAR —
        # using it as the cmdline broke _pid_is_redis and misclassified live
        # orphans as stale_socket (#1642 FIX 5 review P1)).
        start = _parse_lstart(" ".join(parts[3:8])) if len(parts) >= 8 \
            else None
        result[pid] = {
            "etime": _parse_etime(parts[1]),
            "ppid": int(parts[2]) if parts[2].isdigit() else None,
            "start": start,
            "cmdline": parts[8] if len(parts) > 8 else "",
        }
    return result


def _pgrep_redis_servers() -> list[int]:
    """Live redislite redis-server PIDs via pgrep (issue #1005 perf).

    O(servers) instead of scanning the whole tempdir (which holds tens of
    thousands of stale dirs on a leaky machine). Returns [] when pgrep is
    unavailable.
    """
    text = _run_text(
        ["pgrep", "-f", "redislite/bin/redis-server"], timeout=5)
    if text is None:
        return []
    pids: list[int] = []
    for line in text.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _pgrep_redis_servers_or_none() -> list[int] | None:
    """Live redislite redis-server PIDs, or None when the PROBE failed.

    `_pgrep_redis_servers` collapses a timeout, a missing pgrep, or any
    subprocess error into `[]` — indistinguishable from "measured zero".
    A caller that must report an unaccounted residue (#4740: the conftest
    session-end sweep's `left`, which the CI orphan gate binds its bound to)
    needs the difference: `[]` means measured-none, `None` means
    not-measured. Kept alongside (not inside) `_pgrep_redis_servers` so the
    many existing callers keep their exact `[]`-on-failure contract.

    pgrep exits 0 on matches and 1 on no matches — both are successful
    probes. Any other status, a timeout, or an OSError is a failed probe.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "redislite/bin/redis-server"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if out.returncode not in (0, 1):
        return None
    pids: list[int] = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def _socket_dir_from_cmdline(pid: int) -> str | None:
    """Extract the unixsocket dir from a redis-server cmdline.

    Three forms are possible (redislite starts the server as
    `redis-server <redis.config> [--loadmodule ...]`, and the daemonized
    redis re-execs with its effective config as argv):
      1. Inline colon form: `unixsocket:/path/redis.socket` (macOS).
      2. Inline long-form: `--unixsocket /path` (Linux daemonized re-exec).
      3. Config file: the `unixsocket` directive lives in the .config arg
         (pre-re-exec argv) — read it so the live pass is reliable
         regardless of the argv form (#1365: the chaos tests' discover()
         must not silently miss live orphans on one platform).
    """
    cmdline = _cmdline(pid)
    m = _UNIXSOCKET_RE.search(cmdline)
    if m:
        sock = m.group(1)
        return os.path.dirname(os.path.realpath(sock))
    m = _UNIXSOCKET_LONG_RE.search(cmdline)
    if m:
        sock = m.group(1)
        return os.path.dirname(os.path.realpath(sock))
    m = re.search(r"(\S+/redis\.config)\b", cmdline)
    if m:
        try:
            text = Path(m.group(1)).read_text(errors="replace")
            um = re.search(r"^\s*unixsocket\s+'?([^'\s]+)'?", text, re.M)
            if um:
                return os.path.dirname(os.path.realpath(um.group(1)))
        except OSError:
            pass
    return None


def discover(jobs: int = 1, max_tempdir_entries: int = 5000,
             full_scan: bool = False) -> list[dict]:
    """Scan for redislite orphans; return classified records.

    Two passes (issue #1005 perf — the tempdir accumulates tens of thousands
    of stale dirs, making a full walk minutes-long under load):
      1. Live servers via pgrep + cmdline unixsocket extraction — O(servers).
      2. Socket-bearing dirs via a depth-1 `os.scandir` scoped to the
         ephemeral namespace — independent of the tempdir's total entry
         count (#1642 FIX 2: pollution no longer disables cleanup; #4068:
         no `find` subprocess, no depth-2 lstat storm).

    The returned list is a `_ScanAwareList` (still a `list`) whose
    `.complete` flag is False when the bounded scan returned a partial set
    — a truncated scan is never reported as a finished one.

    jobs>1 parallelizes per-dir classification. Fail-closed semantics are
    per-record and unchanged under parallelism.
    """
    tmpdir = _real_gettempdir()

    # Pass 1: live servers (authoritative pid comes from pgrep).
    live_pids = _pgrep_redis_servers()
    seen_dirs: set[str] = set()

    # One batched ps for all live pids (issue #1005 perf).
    global _PROC_INFO_CACHE
    _PROC_INFO_CACHE = _batch_process_info(live_pids)
    try:
        records, complete = _discover_from_live(
            live_pids, jobs, tmpdir, seen_dirs, max_tempdir_entries,
            full_scan=full_scan)
    finally:
        _PROC_INFO_CACHE = {}
    out = _ScanAwareList(records)
    out.complete = complete
    return out


def _discover_from_live(live_pids, jobs, tmpdir, seen_dirs,
                        max_tempdir_entries, full_scan: bool = False):
    """Classification half of discover() (separated so the proc-info cache
    has a deterministic lifetime). Returns ``(records, scan_complete)``."""
    results = []

    def _classify_live(pid: int) -> dict | None:
        sock_dir = _socket_dir_from_cmdline(pid)
        if not sock_dir:
            return None
        socket_path = os.path.join(sock_dir, SOCKET_MARKER)
        # #1383: pass the pgrep pid as known_pid so a stale registry
        # pidfile can never misclassify a LIVE server as stale_socket.
        rec = _classify_dir(sock_dir, socket_path, known_pid=pid)
        if rec is None:
            return None
        rec["pid"] = pid  # pgrep pid is authoritative for live servers
        rec["_live"] = True
        return rec

    if jobs > 1 and len(live_pids) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for rec in pool.map(_classify_live, live_pids):
                if rec is not None:
                    results.append(rec)
                    seen_dirs.add(os.path.dirname(rec["socket_path"]))
    else:
        for pid in live_pids:
            rec = _classify_live(pid)
            if rec is not None:
                results.append(rec)
                seen_dirs.add(os.path.dirname(rec["socket_path"]))

    # Pass 2: tempdir stale-socket scan (stale sockets + synthetic dirs in
    # tests). #1642 FIX 2 (#1449): the scan was previously SKIPPED wholesale
    # when the tempdir exceeded max_tempdir_entries (5000) — pollution
    # disabled the ONLY path that cleans killed-suite residue (chicken-and-
    # egg). #4068: it is now a depth-1 in-process `os.scandir` scoped to the
    # ephemeral namespace (every REMOVAL path requires exactly that predicate
    # at depth 1; kills are covered by the pass-1 lemma — see
    # `_ephemeral_name`), so a 32k-entry tempdir converges without lstat'ing
    # the whole tree. `full_scan=True` restores the pre-#4068 un-scoped
    # enumeration for detection; it adds no reachability an earlier release
    # lacked. max_tempdir_entries is retained for API compatibility but no
    # longer gates the walk.
    scan = _scan_socket_dirs(
        tmpdir, full_scan=full_scan,
        deadline=time.monotonic() + SOCKET_WALK_TIMEOUT)
    socket_dirs = scan.dirs
    dirs = []
    for d in socket_dirs:
        # #1383 plan-review P1: reaper-owned quarantine dirs (*.reaper-
        # stale-*) are handled exclusively by _sweep_quarantine_dirs —
        # never classify them (a guard-7-preserved LIVE server in a moved
        # dir would otherwise classify 'candidate' and be KILLED).
        if STALE_QUARANTINE_SUFFIX in os.path.basename(d):
            continue
        try:
            socket_path = os.path.join(d, SOCKET_MARKER)
            if not os.path.exists(socket_path):
                continue
            if os.path.realpath(d) in seen_dirs:
                continue
            dirs.append((d, socket_path))
        except (PermissionError, OSError):
            logger.warning("dir skipped (OSError): %s", d)
            continue

    if jobs > 1 and len(dirs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [
                pool.submit(_classify_dir, d, s) for d, s in dirs]
            for fut in futures:
                try:
                    rec = fut.result()
                except Exception as exc:  # per-record isolation
                    logger.warning("classification failed: %s", exc)
                    continue
                if rec is not None:
                    results.append(rec)
    else:
        for d, s in dirs:
            try:
                rec = _classify_dir(d, s)
            except Exception as exc:  # per-record isolation
                logger.warning("classification failed: %s", exc)
                continue
            if rec is not None:
                results.append(rec)
    return results, scan.complete


class _ScanResult(NamedTuple):
    """A name-scoped scan's dirs plus whether it ran to completion."""

    dirs: list[str]
    complete: bool


class _ScanAwareList(list):
    """A list of records carrying the discovery scan's `complete` flag.

    #4068: a `list` SUBCLASS, not a new return type — existing callers
    (`len()`, iteration, `== []`, `isinstance(x, list)`) keep working
    unchanged, while the sweep summary can report that a bounded scan
    returned a partial set. Defaults to `False` (fail-closed): a
    construction path that forgets to set it must not read as finished.
    """

    complete: bool = False


def _always_match(name: str) -> bool:
    """`--full-scan` predicate — restores the pre-#4068 un-scoped set."""
    return True


def _ephemeral_name(name: str) -> bool:
    """The depth-1 discovery predicate: the name half of `_is_ephemeral_dir`.

    For a depth-1 entry the two are equivalent, which is why scoping
    discovery to this loses no record any REMOVAL path can act on.

    Losslessness for KILLS rests on a second, independent fact — the pass-1
    lemma: every LIVE server is enumerated by `_pgrep_redis_servers` +
    `_socket_dir_from_cmdline` regardless of its dir name, so a live orphan
    is discovered even when its dir name is outside this namespace. The
    lemma is pinned by `test_live_candidate_is_found_by_pass1_regardless_of_dir_name`.
    """
    return name.startswith(EPHEMERAL_PREFIXES)


def _iter_candidate_dirs(tmpdir: str, *, predicate, deadline=None,
                         ) -> _ScanResult:
    """Depth-1 enumeration of tempdir entries whose NAME matches predicate.

    #4068 — replaces the depth-2 `find` subprocess. The NAME test runs
    FIRST, before `is_symlink()` and before any stat: a foreign entry costs
    one readdir entry, never a metadata call (the old `find` lstats'd ~224k
    entries and held a core for the life of the call). Only name-matching
    entries are then tested for symlink-ness, and a symlinked entry is
    SKIPPED — parity with `find` without `-L`, which does not descend a
    symlinked dir. `deadline` is an ABSOLUTE monotonic cutoff (the
    reap()/_run_sweep convention) sampled every iteration, so an
    already-expired budget always yields `complete=False` + a WARNING.
    """
    dirs: list[str] = []
    complete = True
    try:
        with os.scandir(tmpdir) as it:
            for entry in it:
                if deadline is not None and time.monotonic() >= deadline:
                    complete = False
                    logger.warning(
                        "tempdir scan budget expired — returning partial "
                        "name-scoped set (%d dirs so far)", len(dirs))
                    break
                if not predicate(entry.name):
                    continue
                if entry.is_symlink():
                    continue
                dirs.append(entry.path)
    except OSError as exc:
        logger.warning("tempdir scan failed for %s: %s", tmpdir, exc)
        complete = False
    return _ScanResult(dirs, complete)


def _as_scan_aware(records: list) -> _ScanAwareList:
    """Wrap a discovery result so completeness is always read FAIL-CLOSED.

    A plain `list` (a monkeypatched `discover` seam, or a future caller that
    forgot to set the flag) must never read as a finished scan.
    """
    if isinstance(records, _ScanAwareList):
        return records
    out = _ScanAwareList(records)
    out.complete = False
    return out


def _scan_socket_dirs(tmpdir: str, *, full_scan: bool = False,
                      deadline: float | None = None) -> _ScanResult:
    """Socket/pid-bearing dirs under ``tmpdir``, plus scan completeness.

    #4068: scoped to the ephemeral namespace by default. That is provably
    lossless for every REMOVAL path — each requires `_is_ephemeral_dir`, and
    for a depth-1 dir that is exactly `basename.startswith(EPHEMERAL_PREFIXES)`.
    Losslessness for KILLS rests on the pass-1 lemma (see `_ephemeral_name`):
    a live server is enumerated by pgrep/cmdline regardless of its dir name,
    so the scoped scan losing an out-of-namespace name costs no kill.

    ``full_scan=True`` restores the pre-#4068 UN-SCOPED enumeration. It can
    reach nothing an earlier release could not, and it cannot widen an
    rmtree (containment is re-derived at every removal). It is NOT
    "detect-only" in the strict sense — a live record that only the broad
    scan surfaces is still subject to normal classification and the kill
    path, exactly as before #4068.

    The return value carries ``complete`` so a truncated scan is visible
    rather than reported as a finished one. A ``None`` deadline means "use
    the default budget" — never unbounded (#1642 FIX 2's always-bounded
    intent is preserved for direct callers).
    """
    predicate = _always_match if full_scan else _ephemeral_name
    if deadline is None:
        deadline = time.monotonic() + SOCKET_WALK_TIMEOUT
    res = _iter_candidate_dirs(tmpdir, predicate=predicate, deadline=deadline)
    out: list[str] = []
    for d in res.dirs:
        try:
            if os.path.exists(os.path.join(d, SOCKET_MARKER)) or \
                    os.path.exists(os.path.join(d, PIDFILE_MARKER)):
                out.append(d)
        except OSError:
            continue
    return _ScanResult(sorted(out), res.complete)


def _classify_dir(dbdir: str, socket_path: str,
                  known_pid: int | None = None) -> dict | None:
    """Classify a single candidate dir. Returns record or None (skip)."""
    registry = _registry_for(dbdir)
    dbdir_real = os.path.realpath(dbdir)
    socket_real = os.path.realpath(socket_path)
    tmpdir_real = _real_gettempdir()

    # Authoritative pid: pass-1 live servers supply the pgrep pid (always
    # live — a stale registry pidfile must never misclassify them #1383);
    # pass-2 walk dirs fall back to the registry pidfile.
    pid = known_pid
    if pid is None and registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None

    classification = _classify(socket_real, dbdir_real, tmpdir_real,
                               registry, pid=pid)
    # Issue #1005: servers whose registry dir is gone (pytest cleaned the tmp
    # tree at session end) are always reaping-safe — the owning suite ended.
    reg_dir = (registry or {}).get("dir", (registry or {}).get("dbdir", ""))
    dir_missing = _dir_missing_on_disk(reg_dir)

    uptime = _uptime_seconds(pid) if pid else None
    # #1383: probe-failed client count records None (unknown), never a
    # misleading 0. Only a verified zero is 0.
    client_count = None
    if classification == "candidate" and pid and _pid_alive(pid):
        cc = _active_client_count(socket_real)
        if cc is not None:
            client_count = cc

    return {
        "pid": pid,
        "socket_path": socket_real,
        "dbdir": dbdir_real,
        # #1642 FIX 3: a path-based (user-data) server is NEVER killed
        # without orphan confirmation — reap() gates on this flag.
        "path_based": _is_path_based(registry, dbdir_real, tmpdir_real),
        # #3767: no tortoise-written owner instrument => the server is
        # UNATTRIBUTABLE. reap() requires the #1557/#1642 FIX 3
        # orphan-confirmation window for it: redislite's registry is written
        # by redislite, not tortoise, so it does not establish ownership.
        # #4546 SCOPE: in a FULL sweep reap() exempts a `dir_missing` record
        # from this arm (its registry data dir is already gone, so no on-disk
        # user data remains); the `path_based` arm carries no such exemption,
        # and under `--only-safe` the earlier live-pid gate skips first.
        #
        # RESIDUAL (#3767 review P2, accepted): this covers the DOMINANT route
        # the issue names — a foreign no-path server with an INTACT registry —
        # but only NARROWS it. Such a server is no longer fast-killed; it is
        # still reachable through the confirmation window on a quiet host
        # (`suites_active` False for ZERO_CLIENT_CONFIRM_MINUTES). Closing it
        # outright means making every uninstrumented server permanently
        # unreapable, which strands the raw-redislite test-residue class
        # #1642 FIX 3's fallback exists for — the reaper would be inert. The
        # issue's ordering note asks for the discriminator to be tightened
        # first, which this does; the residual is recorded, not hidden.
        "unattributed": not _owner_record_dir_present(dbdir_real),
        "dir_missing": dir_missing,
        "client_count": client_count,
        "uptime": uptime,
        "classification": classification,
        "settings": registry,
    }


def _is_path_based(registry: dict | None, dbdir_real: str,
                   tmpdir_real: str) -> bool:
    """True when the registry signals a USER-path (non-ephemeral) server.

    Mirrors _classify's protection signals (Signal 1 registry dir, Signal 2
    user dbfilename, old-format .db presence). reap() refuses to kill a
    path_based server unless orphanhood is confirmed (persisted 0-client
    state — #1642 FIX 3); ephemeral test-tree servers keep the fast full-
    sweep kill contract — UNLESS they are `unattributed` (#3767), in which
    case reap() applies the same confirmation requirement.

    OVERRIDES (#3767): with NO registry record there is no signal to read, so
    this returns True (fail CLOSED) rather than False — the registry is the
    authoritative path-based discriminator (see _classify), and without it a
    disposable no-path server cannot be proven. That deliberately engages the
    every-mode orphan-confirmation requirement the pre-#3767 `return False`
    disengaged.

    #4546 SCOPE: reap()'s every-mode confirmation requirement still applies
    to `path_based` unchanged, but in a FULL sweep its `unattributed` half
    exempts a `dir_missing` record (the registry data dir is already gone, so
    no on-disk user data remains — pre-#3767 behaviour). Under `--only-safe`
    the exemption is unreachable: the earlier live-pid gate skips every
    non-orphan-confirmed candidate first, so `--only-safe` is unchanged.

    CONSEQUENCE (#3767 review P2, documented not silent): `path_based` is ALSO
    the gate `_mark_orphan_confirmation` uses to admit the #3599 per-server
    fast confirmation (`no_live_owner = ... and not path_based`), so a
    registry-less but tortoise-instrumented live orphan — an instrument whose
    redislite registry file was removed while its socket dir survived — no
    longer takes the fleet-independent "every owner provably dead" path and
    converges only through the #1557/#1642 FIX 3 confirmation window (which
    `suites_active` blocks on a host with a live suite marker). That is the
    fail-CLOSED direction: the window's blast-radius restriction to provably
    ephemeral servers is a #1642 FIX 3 decision, and a missing registry cannot
    prove ephemerality. It is not a kill-path regression — the class is
    admitted and still reapable via the window.
    """
    if not registry:
        return True
    reg_dbdir = registry.get("dir", registry.get("dbdir", ""))
    reg_dbdir_real = os.path.realpath(reg_dbdir) if reg_dbdir else ""
    if reg_dbdir_real and not _is_ephemeral_dir(reg_dbdir_real, tmpdir_real):
        return True  # Signal 1: user data dir
    db_filename = registry.get("dbfilename", "")
    if db_filename and db_filename != "redis.db" \
            and not _is_ephemeral_dir(reg_dbdir_real, tmpdir_real):
        return True  # Signal 2: user db filename
    if "dbfilename" in registry and registry.get("dbfilename") is None:  # noqa: SIM102
        if _dir_has_db_file(dbdir_real) \
                and not _is_ephemeral_dir(dbdir_real, tmpdir_real):
            return True  # old-format path-based
    return False


def _classify(socket_real: str, dbdir_real: str, tmpdir_real: str,
              registry: dict | None, pid: int | None = None) -> str:
    """Dual-signal classification (plan Task 1).

    Signal 1 — registry dbdir: path-based servers register a USER directory
    (e.g. /tmp, /Users/...); no-path servers register an auto-generated
    tempdir (tmpXXXX under TMPDIR).
    Signal 2 — registry dbfilename: path-based -> user filename
    (e.g. pathbased_reaper_test.db); no-path -> the generic 'redis.db'.
    Both signals must agree for 'candidate'.

    Note: redislite ALWAYS places the unix socket in a tempdir (even for
    path-based servers), so socket location alone is insufficient — the
    registry is the authoritative source.
    """
    # No registry at all -> cannot confirm path-based; treat conservatively
    # via dirname pattern + .db-file presence (old-format fallback).
    if registry is None:
        if _dir_has_db_file(dbdir_real) and not _is_ephemeral_dir(dbdir_real, tmpdir_real):
            return "protected"
        basename = os.path.basename(dbdir_real)
        if not (_AUTOGEN_DIRNAME.match(basename)
                or _is_ephemeral_dir(dbdir_real, tmpdir_real)):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", dbdir_real)
            return "protected"
        # #1642 FIX 2: a provably-DEAD pid is leftover residue (the
        # killed-suite class). It needs no ownership claim — there is no live
        # server to mis-identify — and must keep classifying 'stale_socket'
        # so the walk still converges on it.
        if pid is not None and not _pid_effectively_alive(pid):
            return _cooldown_check(registry, pid=pid)
        # #3767 OWNERSHIP CLAIM. The remaining admission is a LIVE server in
        # an auto-generated-named / tempdir-contained dir: exactly the shape
        # another same-uid redislite application, a second tortoise instance,
        # or another tenant sharing TMPDIR also produces. Name + containment
        # is not ownership, so require the positive claim; without it the dir
        # is not a candidate at all (not merely deferred to a later gate).
        refusal = _has_ownership_claim(dbdir_real, pid)
        if refusal is not None:
            logger.warning(
                "no ownership claim for registry-less dir (%s), treating "
                "as protected: %s", refusal, dbdir_real)
            return "protected"
        return _cooldown_check(registry, pid=pid)

    # Signal 1: registry 'dir' is a USER dir (not auto tempdir) -> path-based.
    reg_dbdir = registry.get("dir", registry.get("dbdir", ""))
    reg_dbdir_real = os.path.realpath(reg_dbdir) if reg_dbdir else ""
    # Issue #1005: ephemeral TEST tmp trees (tortoise_*, tt_*, pytest-of-*,
    # pack_v3_bad_*) count as autogen — servers rooted there are disposable
    # once their clients are gone. User-home dirs stay protected (the
    # tempdir-containment check below is the safety boundary).
    is_autogen_dbdir = bool(
        reg_dbdir_real
        and _is_ephemeral_dir(reg_dbdir_real, tmpdir_real)
    )
    if not is_autogen_dbdir and reg_dbdir_real:
        if reg_dbdir_real.startswith(tmpdir_real):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", reg_dbdir_real)
        # Issue #1642 FIX 3 (#1427 circularity): redislite's registry
        # pidfile is the server's OWN pid (redis.pid), so the #1427 owner
        # check is True for every live server — it protected orphaned
        # path-based servers forever. A LIVE server's orphanhood is decided
        # from detachment (ppid=1) + persisted 0-client CLIENT LIST state
        # (_mark_orphan_confirmation + reap()'s double-check), so the server
        # is admitted as a candidate (boot cooldown still applies). The
        # #1427 dead-owner reclassification below keeps the stale path.
        if pid is not None and _pid_effectively_alive(pid):
            return _cooldown_check(registry, pid=pid)
        # Issue #1427: a path-based server whose registry-recorded owner pid
        # is provably dead (or recycled — FIX 5) is an orphan — the db file
        # has no live owner, so the server is a leftover, not live data.
        # Unresolvable owners (None) fail closed below.
        if _registry_owner_alive(registry) is False:
            return _cooldown_check(registry, pid=pid)
        return "protected"

    # Signal 2: dbfilename is the generic 'redis.db' (no-path) vs user name.
    # Issue #1005: in an ephemeral TEST tree the filename signal does not
    # protect — the tree is disposable regardless of the db filename.
    db_filename = registry.get("dbfilename", "")
    if db_filename and db_filename != "redis.db" \
            and not _is_ephemeral_dir(reg_dbdir_real, tmpdir_real):
        # Issue #1642 FIX 3: same live-server restructure as Signal 1 — a
        # live pid admits the server as a candidate (orphanhood decided by
        # the 0-client confirmation); a dead/recycled owner reclassifies
        # below (#1427). #1383 review: known_pid pass-through (see Signal 1).
        if pid is not None and _pid_effectively_alive(pid):
            return _cooldown_check(registry, pid=pid)
        if _registry_owner_alive(registry) is False:
            return _cooldown_check(registry, pid=pid)
        return "protected"

    # Old-format registry (no dbfilename field): .db file present -> protected.
    if "dbfilename" in registry and registry.get("dbfilename") is None:
        if _dir_has_db_file(dbdir_real) and not _is_ephemeral_dir(dbdir_real, tmpdir_real):
            # Issue #1642 FIX 3: same live-server restructure — old-format
            # path-based servers are the same protection class. #1383
            # review: known_pid pass-through (see Signal 1 comment).
            if pid is not None and _pid_effectively_alive(pid):
                return _cooldown_check(registry, pid=pid)
            if _registry_owner_alive(registry) is False:
                return _cooldown_check(registry, pid=pid)
            return "protected"
        basename = os.path.basename(dbdir_real)
        if not (_AUTOGEN_DIRNAME.match(basename)
                or _is_ephemeral_dir(dbdir_real, tmpdir_real)):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", dbdir_real)
            return "protected"

    return _cooldown_check(registry, pid=pid)


def _cooldown_check(registry: dict | None,
                    pid: int | None = None) -> str:
    """Boot-cooldown: fresh servers (uptime < MIN_UPTIME) are protected.

    #1383: a DEAD authoritative pid (Z-aware) classifies 'stale_socket' —
    a leftover dir no process owns, reapable by guarded rmtree — instead of
    a phantom 'candidate' reap()'s liveness-first gate can never act on.
    """
    if pid is None and registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None
    if pid is not None and not _pid_effectively_alive(pid):
        return "stale_socket"
    # DELIBERATE (pre-existing, not a #4496 regression): an UNMEASURABLE
    # uptime (`None` — a `ps` timeout/OSError, or a non-text stdout read as
    # undeterminable) is NOT treated as protected. The boot cooldown is a
    # CLOCK guard, and `"candidate"` is only admission to `reap()`'s kill
    # path — where the live-pid / orphan-confirmation / 0-client /
    # owner-record / held-flock / provenance gates still decide. Classifying a
    # failed probe as `protected` instead would skip the record at `reap()`'s
    # `classification != "candidate"` short-circuit and make the whole reaper
    # inert whenever `ps` is slow or absent — the #3599 failure class.
    # `_run_text` turning a bad stdout into `None` widened only WHICH inputs
    # reach this path, not its semantics (a real `ps` timeout already produced
    # `None` here).
    uptime = _uptime_seconds(pid) if pid else 0.0
    min_uptime = _parse_min_uptime()
    if uptime is not None and uptime < min_uptime:
        return "protected"
    return "candidate"


def _dir_has_db_file(dbdir: str) -> bool:
    try:
        for p in Path(dbdir).glob("*.db"):  # noqa: B007
            return True
    except OSError:
        pass
    return False


# ── Phase 1 / Phase 2 discovery helpers (plan Task 2) ───────────────

def phase1_probe(record: dict) -> dict:
    """Ordered discovery: resolve stale-PID sockets via raw probe FIRST.

    #1383 contract update: only a confirmed 'dead' socket (ECONNREFUSED —
    the socket FILE exists, no listener) classifies 'stale_socket';
    'missing' (vanished socket — mid-startup) and 'undetermined' fail
    closed. An 'alive' socket upgrades to 'candidate' with the real pid
    derived (live orphan — kill semantics). A record whose pid became
    alive since discovery is reclassified 'candidate' (a live server must
    never stay stale_socket).
    """
    if record.get("pid") and _pid_alive(record["pid"]):
        if record.get("classification") != "candidate":
            record["classification"] = "candidate"
        return record
    if not record.get("socket_path"):
        return record
    # #1383 security review (Issue 2): a connect-only verdict needs no
    # response — use the SHORT socket timeout so a hostile full-backlog
    # socket farm cannot hang the sweep (2.0s x N dirs would trip the
    # 120s SIGALRM and abort every cron run).
    probe = _probe_socket(record["socket_path"],
                          timeout=PROBE_SOCKET_TIMEOUT)
    if probe == "dead":
        record["classification"] = "stale_socket"
    elif probe == "alive":
        real_pid = _derive_real_pid(record["socket_path"], record.get("pid"))
        if real_pid:
            record["pid"] = real_pid
            record["classification"] = "candidate"
        else:
            record["classification"] = "undetermined"
    else:  # missing / undetermined
        record["classification"] = "undetermined"
    return record


def reap(records: list[dict], dry_run: bool = True, batch_size: int | None = None,
         sigterm_timeout: float = 10.0, kill_pacing: float = KILL_PACING_DEFAULT,
         only_safe: bool = False, jobs: int = 8,
         deadline: float | None = None) -> list[dict]:
    """Reap records safely (#1383: the two-verb action engine).

    Returns acted-upon list.

    - 'candidate' records are KILLED, and only after CLIENT LIST shows 0
      active clients (double-checked). Path-based / protected /
      undetermined records are NEVER acted on.
    - 'stale_socket' records (dead-pid leftover dirs) are REMOVED via the
      guarded rmtree action _remove_stale_socket_dir — a 9-guard chain
      (containment -> pidfile re-read -> ECONNREFUSED-only socket re-probe
      -> mtime age x2 -> atomic rename-aside -> post-rename re-probe ->
      moved-pidfile check -> rmtree of the renamed path only). Stale
      removals never consume the kill batch_size and are capped by
      STALE_SWEEP_BUDGET; they run in every mode including only_safe (the
      guards ARE the safety) and dry_run (reported, not mutated).

    only_safe=True (issue #1005 concurrency guard): NEVER kills a live-pid
    server that is not orphan-CONFIRMED — redislite servers daemonize to
    ppid=1, so _is_detached is True for ALL of them and the reaper cannot
    distinguish a concurrent suite's live test server from a killed-
    subprocess orphan on pid/detachment alone (#1557). A live server is
    killed only when `_orphan_confirmed`, which post-#3599 means EITHER:
      - a per-server signal: every owner record in the server's socket dir
        belongs to a provably-dead pid, and the server is ephemeral
        (`path_based` False). This confirms on the FIRST sweep and is
        independent of other suites on the host — that independence is the
        whole point of #3599, because the global gate below is permanently
        True on a fleet box; or
      - the #1642 FIX 3 fallback for uninstrumented spawns (no owner
        records at all): a persisted 0-client CLIENT LIST state >= 10 min
        with no live suite markers (set by _mark_orphan_confirmation in
        _run_sweep).
    Path-based (user-data) servers additionally require confirmation in
    EVERY mode (their data outlives the test tree). In a FULL sweep
    (`only_safe=False`) a `dir_missing` record is exempt from the
    `unattributed` half of that requirement (#4546: its data dir — and
    therefore all on-disk user data — is already gone); the `path_based` half
    is NOT exempted, and under `--only-safe` the earlier live-pid gate still
    skips every non-orphan-confirmed candidate, so `--only-safe` is unchanged.
    This preserves the
    #1005 guarantee: a concurrent suite's between-tests idle server is
    never disturbed.
    """
    # #4438 review P2: `jobs` reaches `ThreadPoolExecutor(max_workers=...)`
    # below, where `min(jobs, len(...))` makes a non-positive `--jobs`
    # (0 or -1) a hard crash (`max_workers must be greater than 0`). The
    # documented CLI flag must be safe on its own — the same standard as
    # `_parse_timeout` — so clamp here too, for direct callers.
    if jobs < 1:
        logger.warning("jobs=%r must be >= 1 — using 1", jobs)
        jobs = 1
    acted = []
    killed = 0
    stale_removed = 0  # #1383: stale removals budgeted separately from kills
    # #1642 perf: pre-probe the CLIENT LIST before-counts of every candidate
    # in PARALLEL (raw unix-socket probes are thread-safe and read-only).
    # The old per-record serial double-check made a sweep over hundreds of
    # servers minutes-long on a loaded box (each probe can take up to ~1-3s
    # against an unresponsive server) — parallel probes cut it to seconds
    # while the kills stay serial + paced (#1005). The loop's gates (budget,
    # only_safe, orphan confirmation) still run in order; fail-closed
    # semantics are per-record and unchanged (None -> skip). The per-kill
    # AFTER probe remains a fresh serial probe (a server can gain a client
    # between the pre-probe and its kill).
    _client_before_cache: dict[int, int | None] = {}
    candidate_records = [r for r in records
                         if r.get("classification") == "candidate"
                         and r.get("socket_path")]
    # The eager parallel pre-probe is skipped for a DEADLINE-bound sweep:
    # it submits CLIENT LIST probes for the ENTIRE candidate list before
    # joining — with a large backlog (stale records + a suite's live
    # servers on a shared runner) that join alone can run past pytest-
    # timeout (observed: >300s in the conftest session-end sweep, killing
    # the leg mid-reap). The per-record probes in the loop below are gated
    # by the deadline and abort instead.
    if deadline is None and candidate_records and len(candidate_records) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(
                max_workers=min(jobs, len(candidate_records))) as pool:
            futures = [(id(r), pool.submit(_active_client_count,
                                           r["socket_path"]))
                       for r in candidate_records]
            _client_before_cache = {rid: f.result() for rid, f in futures}
    for record in records:
        if deadline is not None and time.monotonic() >= deadline:
            logger.info("sweep deadline hit (%.1fs elapsed) — %d acted, "
                        "stopping; cron sweeps clear the remainder",
                        time.monotonic(), len(acted))
            break
        classification = record.get("classification")
        if classification == "stale_socket":
            if stale_removed >= STALE_SWEEP_BUDGET:
                continue  # budget exhausted — remainder converges next sweep
            # #1383: dead-pid leftover dir — no process to kill; guarded
            # rmtree (see _remove_stale_socket_dir). Safe under only_safe
            # by construction (the guards re-verify deadness at action time).
            acted_rec = _remove_stale_socket_dir(record, dry_run)
            if acted_rec is not None:
                acted.append(acted_rec)
                stale_removed += 1
            continue
        if classification != "candidate":
            if classification in ("protected", "undetermined"):
                logger.warning(
                    "skipping path-based/non-candidate server: %s",
                    record.get("socket_path"))
            continue
        # Kill budget: bounds process kills only (bgsave-storm semantics).
        # Stale cleanup above is budgeted separately (STALE_SWEEP_BUDGET).
        # `continue` (not break) so interleaved stale records after the
        # budget is exhausted are still processed (#1383 branch ordering).
        if batch_size is not None and killed >= batch_size:
            continue
        if only_safe and not (record.get("dir_missing")
                              or record.get("_orphan_confirmed")
                              or _is_detached(record.get("pid") or 0)):
            logger.info(
                "concurrent-suite guard: skipping live ephemeral candidate %s",
                record.get("socket_path"))
            continue

        # #1642 FIX 3: orphanhood for a LIVE-pid server is decided by
        # detachment + persisted 0-client CLIENT LIST state (`_orphan_
        # confirmed`, set by _mark_orphan_confirmation in _run_sweep), never
        # by the registry pidfile (redislite writes the server's OWN pid
        # there — the #1427 owner=self-pid circularity protected orphaned
        # path-based servers forever). Gates:
        #   - only_safe: a live-pid server is killed ONLY when confirmed
        #     (a concurrent suite's between-tests idle server is also
        #     0-client + detached — #1557; the confirmation wait + no-live-
        #     markers guard distinguishes them).
        #   - path_based (user-data) server: confirmation required in EVERY
        #     mode — its data outlives the test tree.
        if record.get("pid") and _pid_alive(record["pid"]):  # noqa: SIM102
            if not record.get("_orphan_confirmed"):
                if only_safe:
                    logger.info(
                        "concurrent-suite guard: live-pid server not "
                        "orphan-confirmed under only_safe, skipping %s",
                        record.get("socket_path"))
                    continue
                # #3767: 'path_based' OR 'unattributed' — a server with no
                # tortoise-written owner instrument cannot be attributed to
                # us, so it needs the same confirmation as a user-data server
                # in EVERY mode (not only under --only-safe). It remains
                # reapable via the #1557/#1642 FIX 3 window.
                #
                # #4546 EXEMPTION — OVERRIDES: the #3767 rule that an
                # `unattributed` server (no `.tortoise-owners` instrument)
                # needs the #1557/#1642 FIX 3 confirmation window in EVERY
                # mode — which itself carries #1642 FIX 3's "a directory that
                # is gone keeps the confirmation window" stance — for a
                # `dir_missing` record ONLY. `dir_missing` means the REGISTRY
                # data dir (the pytest `tmp_path` tree) is already gone, so no
                # on-disk user data remains for the window to protect, and
                # pre-#3767 `main` fast-killed the class. This does NOT touch
                # #1642 FIX 3's socket-dir-unlinked window: such a record has
                # no registry, so `_is_path_based` fail-closes True and the
                # untouched `path_based` arm still gates it. The exemption is
                # scoped to the NEW `unattributed` signal this branch
                # introduced; the pre-existing `path_based` arm is
                # deliberately left intact, so a genuinely path-based
                # (user-data) server still requires confirmation in EVERY mode
                # exactly as before. #3767's target (unowned/foreign server,
                # directory PRESENT, registry intact) reports
                # `dir_missing=False` and keeps the gate.
                #
                # NARROWNESS (#4546): keyed on the registry-data-dir property
                # alone. Do NOT widen to `unattributed` generally — that
                # would admit a dir-present foreign server, which is the
                # negative control in tests/test_reaper_ownership.py.
                if record.get("path_based") or (
                        record.get("unattributed")
                        and not record.get("dir_missing")):
                    logger.info(
                        "path-based/unattributed server not orphan-confirmed, "
                        "skipping %s", record.get("socket_path"))
                    continue

        # Liveness-first: never kill a dead PID's leftovers via connect.
        if not record.get("pid") or not _pid_alive(record["pid"]):
            logger.warning("dead pid, skipping: %s",
                           record.get("socket_path"))
            continue

        # #1642 FIX 3: a CONFIRMED socket-less orphan (socket dir GONE —
        # no client can exist and no probe can succeed) skips the CLIENT
        # LIST gates: by the time this branch is reached the missing-dir
        # signal has satisfied the pre-existing confirmation requirement
        # (the 10-min window + (pid, start) identity + no live suite
        # markers) — #3599 deliberately did NOT make a missing dir an
        # immediate signal, because a live server whose dir was unlinked
        # still serves established connections.
        socketless = (record.get("_orphan_confirmed")
                      and _socket_dir_missing(record["socket_path"]))

        # Double-check CLIENT LIST (before+after). The before-count comes
        # from the parallel pre-probe cache when available (the loop's gates
        # may skip records the pre-probe covered — fine); a live fallback
        # covers direct reap() calls with uncached records.
        clients_before = 0 if socketless else _client_before_cache.get(
            id(record), _active_client_count(record["socket_path"]))
        if clients_before is None:
            logger.warning(
                "CLIENT LIST probe failed, skipping (fail closed): %s",
                record.get("socket_path"))
            continue
        if clients_before > 0:
            logger.info("server has %d active client(s), skipping: %s",
                        clients_before, record["socket_path"])
            continue
        clients_after = (0 if socketless
                         else _active_client_count(record["socket_path"]))
        if clients_after is None:
            logger.warning(
                "CLIENT LIST re-probe failed, skipping (fail closed): %s",
                record.get("socket_path"))
            continue
        if clients_after > 0:
            logger.info("server gained a client between checks, skipping: %s",
                        record["socket_path"])
            continue

        # #3599 adversarial review (fail-open): the per-server owner signal is
        # what AUTHORISES this kill, but it was read once, back in
        # `_mark_orphan_confirmation`. A co-tenant that attached since then
        # would not block: `_active_client_count` ignores connections younger
        # than its age floor, and on the socketless path both probes are
        # skipped entirely. So re-read the owner records immediately before
        # the kill and refuse if any owner is now live. Cheap (one listdir +
        # kill(0) per record) and it makes the decision self-consistent:
        # if we confirm on "no live owner", we must not kill on "a live
        # owner appeared". An uninstrumented server has no records (None ->
        # unchanged) and a socketless one cannot host a new record's dir.
        # #4577: the same self-consistency re-check for the KERNEL lock — a
        # live owner that attached since confirmation holds a shared flock
        # on the owner dir's `.lock`. True -> never kill; False/None -> the
        # record-based evidence chain below still decides (a free lock alone
        # never authorizes a kill). Checked BEFORE `_owner_records` because
        # it is the stronger signal and needs no pid/start parsing.
        if _owner_lock_held(record["socket_path"]) is True:
            logger.info(
                "owner lock held (live owner), skipping: %s",
                record["socket_path"])
            continue
        owners_now = _owner_records(record["socket_path"])
        if owners_now is not None and owners_now[0] > 0:
            logger.info(
                "owner attached since confirmation (%d live), skipping: %s",
                owners_now[0], record["socket_path"])
            continue

        # #4136 provenance guard — the SIGTERM authority is exercised HERE,
        # so the check lives here (not at discovery): a foreign-authored
        # candidate dir cannot authorize a kill (T2), and a dir swapped
        # since discovery is refused (T4). A vanished dir must be bound to
        # the live process that names it (socket-less orphans, #1642 FIX 3).
        refusal = _kill_provenance_refusal(record)
        if refusal is not None:
            logger.warning(
                "refusing to kill PID %s: %s — provenance guard (#4136); "
                "skipping %s", record.get("pid"), refusal,
                record["socket_path"])
            continue

        if dry_run:
            logger.warning("[DRY-RUN] would kill PID %s (%s)",
                           record["pid"], record["socket_path"])
            acted.append(record)
            continue

        _kill(record["pid"], sigterm_timeout)
        # #1383 security review (Issue 1): the KILL path's tempdir cleanup
        # must honor the same containment discipline as the stale path — a
        # pgrep-decoy's crafted dbdir must never be rmtree'd. Legit kills
        # always target ephemeral test trees, so this breaks nothing.
        # #1642 FIX 2: remove BOTH the socket dir (record dbdir) AND the
        # registry's data dir when they are ephemeral test trees — a kill
        # previously left the data dir (e.g. a tortoise_test_x_* path) as a
        # permanent tempdir entry (observed: 32k entries). User-path data
        # dirs fail the ephemeral containment check and are preserved.
        dbdir = record.get("dbdir")
        reg_dir = (record.get("settings") or {}).get(
            "dir", (record.get("settings") or {}).get("dbdir", ""))
        tmpdir_real = os.path.realpath(tempfile.gettempdir())
        for d in dict.fromkeys([dbdir, reg_dir]):
            if not d:
                continue
            if _is_ephemeral_dir(os.path.realpath(d), tmpdir_real):
                _cleanup_tempdir(d)
            else:
                logger.warning("kill path: skipping tempdir cleanup for "
                               "non-ephemeral dir %r", d)
        logger.warning("killed orphan PID %s (%s)",
                       record["pid"], record["socket_path"])
        acted.append(record)
        killed += 1
        if kill_pacing > 0:
            time.sleep(kill_pacing)  # avoid synchronized shutdown bursts (#1005)
    return acted


def _remove_stale_socket_dir(record: dict, dry_run: bool) -> dict | None:
    """Reap a stale_socket record: guarded rmtree of the leftover dir.

    #1383 — the FIRST reap() action gated on negative evidence, so the
    chain re-verifies deadness at action time (TOCTOU discipline, #1231
    template). Every abort = WARNING + no partial delete (re-verified next
    sweep). The rename-aside + post-rename re-probe convert the worst case
    (delete a live server's data) into 'leave a quarantined dir'.

    The 9 guards:
      0. re-entry: dbdir already carrying the quarantine suffix is
         reaper-owned — handled exclusively by _sweep_quarantine_dirs
      1. already gone  -> reported acted (no error)
      2. containment: _is_ephemeral_dir under the tempdir (semi-public
         reap() must refuse crafted/errant records)
      3. pidfile re-read: a now-LIVE pid = respawn/pid-reuse -> abort
      4. socket re-probe (short timeout): only ECONNREFUSED ('dead' — file
         exists, no listener) proceeds; 'missing'/'alive'/'undetermined'
         all fail closed
      5. mtime age guard x2 (boot window; the second stat narrows the
         create-during-guard-chain window)
      6. atomic rename-aside to <dir>.reaper-stale-<ns>; rename OSError
         -> abort, dir intact
      7. post-rename socket re-probe on the MOVED socket (a server that
         moved with its dir still answers) — live -> leave quarantine
      8. moved pidfile re-read: a live pid written during the window
         (backlog-full ECONNREFUSED hardening) -> leave quarantine
      then rmtree ONLY the renamed path; partial rmtree leftovers converge
      via the quarantine sweep on the next pass.
    """
    dbdir = record.get("dbdir")
    socket_path = record.get("socket_path")
    if not dbdir or not socket_path:
        logger.warning("stale_socket record missing dbdir/socket, skipping")
        return None
    dbdir_real = os.path.realpath(dbdir)
    # Guard 0 (re-entry, plan-review P1): a dir that ALREADY carries the
    # quarantine suffix is reaper-owned — handled exclusively by
    # _sweep_quarantine_dirs; never rename it a second time.
    if STALE_QUARANTINE_SUFFIX in os.path.basename(dbdir_real):
        logger.warning("quarantine dir passed to stale action, skipping: %s",
                       dbdir_real)
        return None
    # Guard 1: containment — reap() is semi-public; never delete outside
    # the ephemeral tempdir tree (classification already enforces this for
    # discover() records; guard against crafted/errant direct records).
    # Evaluated BEFORE the already-gone fast path: a crafted record pointing
    # at a nonexistent path outside the tempdir must NOT be reported acted
    # (plan-review cycle 3 — containment is the honest verdict).
    if not _is_ephemeral_dir(dbdir_real, _real_gettempdir()):
        logger.warning("stale dir outside ephemeral tempdir, skipping: %s",
                       dbdir_real)
        return None
    # Guard 2: already gone (an ephemeral dir that vanished between
    # discovery and action is reported acted — nothing left to delete)
    if not os.path.exists(dbdir_real):
        logger.info("stale dir already gone: %s", dbdir_real)
        return record
    # Guard 3: re-read the pidfile — a now-live pid means respawn/pid-reuse
    # (#1642 FIX 5: only a LIVE REDIS process aborts — an alive non-redis
    # pid is a recycled number, provably not the recorded server, so the
    # socket re-probe below remains the real gate).
    pid = None
    pidfile = os.path.join(dbdir_real, PIDFILE_MARKER)
    try:
        pid = int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_effectively_alive(pid):
        logger.warning("stale dir pidfile now a live redis-server (%s), "
                       "skipping: %s", pid, dbdir_real)
        return None
    # Guard 4: re-probe the socket with the SHORT timeout. Only
    # 'dead' (ECONNREFUSED — socket file exists, no listener) proceeds;
    # 'missing' (mid-startup), 'alive', 'undetermined' all fail closed.
    probe = _probe_socket_any(socket_path, timeout=PROBE_SOCKET_TIMEOUT)
    if probe != "dead":
        logger.warning("stale dir socket probe %s, skipping: %s",
                       probe, dbdir_real)
        return None
    # Guard 5: mtime age guard (boot window) + re-stat right before rename
    # (the second stat narrows the create-during-guard-chain window)
    try:
        age = time.time() - os.stat(dbdir_real).st_mtime
    except OSError:
        return None
    if age < STALE_SOCKET_MIN_AGE_DEFAULT:
        logger.info("stale dir too young (%.1fs), skipping: %s",
                    age, dbdir_real)
        return None
    try:  # re-stat immediately before the irreversible rename
        age = time.time() - os.stat(dbdir_real).st_mtime
        if age < STALE_SOCKET_MIN_AGE_DEFAULT:
            logger.info("stale dir mtime changed mid-chain, skipping: %s",
                        dbdir_real)
            return None
    except OSError:
        return None
    # Guard 5.5 (#4136): PROVENANCE. Every guard above is satisfied by
    # evidence the candidate dir's owner authored; ownership of the
    # directory is the one property a foreign uid cannot forge. Checked on
    # the RAW record path with no-follow (NOT the realpath'd `dbdir_real`),
    # so a path swapped for a symlink since discovery is refused (T4)
    # rather than resolved onto whatever it now points at. Placed at the
    # action — immediately before the first mutation (the marker write) and
    # before the dry-run branch, so the reported set equals the acted set.
    if not _dir_owned_by_euid(dbdir):
        logger.warning(
            "stale dir %r is not a directory owned by euid %d (owner %s) — "
            "provenance guard (#4136), skipping", dbdir, os.geteuid(),
            _dir_owner_of(dbdir))
        return None
    if dry_run:
        logger.warning("[DRY-RUN] would remove stale socket dir %s", dbdir_real)
        return record
    # Guard 6/7: atomic rename-aside then re-verify BEFORE the irreversible
    # rmtree. The renamed dir is the quarantine — if re-verification fails,
    # the dir stays for operator inspection / next-sweep convergence.
    # The reaper-owned marker is written BEFORE the rename (review P2): a
    # marker-write failure AFTER the rename would leave a suffix dir with no
    # marker that NO handler can reclaim — discover pass 2 skips quarantine
    # suffix dirs, _sweep_quarantine_dirs requires the marker, and guard 0
    # rejects the suffix — a permanent leak. Pre-rename failure aborts with
    # the dir intact (retried next sweep); a stray marker on a later rename
    # failure is inert (redis ignores unknown files; the marker is simply
    # re-written on the next successful rename).
    #
    # #4098 (CWE-377 / SEI CERT FIO21-C): the candidate dir is discovered
    # in a SHARED, world-writable tempdir (Linux `/tmp`, mode 1777), so the
    # marker write must never follow a symlink the dir's owner planted
    # there — a plain `open(path, "w")` turns "write a marker" into
    # "truncate any file the reaper's uid can write". O_NOFOLLOW alone
    # protects only the basename, so the dir is opened O_NOFOLLOW and the
    # marker is addressed RELATIVE to that fd (the openat pattern;
    # CVE-2018-6954 is the precedent for skipping it). The dir open needs
    # READ permission, so a candidate dir that is write+execute but
    # non-readable (0300) is abandoned rather than reaped — fail-closed, and
    # unreachable for redislite/mkdtemp dirs (0700).
    try:
        dir_fd = os.open(dbdir_real,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        logger.warning("stale dir unopenable (%s), skipping: %s",
                       exc, dbdir_real)
        return None
    try:
        marker_fd = _open_marker_no_follow(dir_fd)
        if marker_fd is None:
            logger.warning("could not write reaper marker, aborting: %s",
                           dbdir_real)
            return None
        try:
            if os.write(marker_fd, b"reaper-owned\n") != len(
                    b"reaper-owned\n"):
                raise OSError("short marker write")
        finally:
            try:  # noqa: SIM105
                os.close(marker_fd)
            except OSError:
                pass
    except OSError:
        # Pre-#4098 semantics preserved: a marker write/close failure skips
        # THIS record (the dir is retried next sweep) — it must never abort
        # the whole sweep, whose remaining records include live orphans.
        logger.warning("could not write reaper marker, aborting: %s",
                       dbdir_real)
        return None
    finally:
        try:  # noqa: SIM105
            os.close(dir_fd)
        except OSError:
            pass
    renamed = dbdir_real + STALE_QUARANTINE_SUFFIX + str(time.time_ns())
    try:
        os.rename(dbdir_real, renamed)
    except OSError as exc:
        logger.warning("stale dir rename failed (%s), skipping: %s",
                       exc, dbdir_real)
        return None
    renamed_sock = os.path.join(renamed, SOCKET_MARKER)
    if not os.path.exists(renamed_sock):
        logger.warning("quarantined socket vanished, leaving dir: %s", renamed)
        return None  # leave quarantine (next sweep re-probes)
    if _probe_socket_any(renamed_sock, timeout=PROBE_SOCKET_TIMEOUT) != "dead":
        logger.warning("quarantined socket live, leaving dir: %s", renamed)
        return None  # live server in the moved dir — do NOT delete
    # Guard 8: pidfile written during the window (backlog-full ECONNREFUSED
    # hardening — a live server that refuses connects can still write its pid)
    try:
        moved_pid = int(Path(os.path.join(renamed, PIDFILE_MARKER)).read_text().strip())
    except (OSError, ValueError):
        moved_pid = None
    if moved_pid is not None and _pid_effectively_alive(moved_pid):
        logger.warning("quarantined pidfile now a live redis-server (%s), "
                       "leaving dir: %s", moved_pid, renamed)
        return None
    if not _cleanup_tempdir(renamed):
        logger.warning("provenance guard refused quarantine cleanup, "
                       "leaving dir: %s", renamed)
        return None
    if os.path.exists(renamed):
        logger.warning("partial rmtree leftover, will re-probe next sweep: %s",
                       renamed)
    logger.warning("removed stale socket dir %s (was %s)", renamed, dbdir_real)
    # Acted record: `dbdir` stays the ORIGINAL (pre-rename) path so all
    # existing acted assertions hold; the renamed/quarantined path rides in
    # `removed_dir` for --json correlation (plan-review cycle 2).
    return {**record, "removed_dir": renamed}


def _open_marker_no_follow(dir_fd: int) -> int | None:
    """Open REAPER_OWNED_MARKER inside ``dir_fd`` for writing, never
    following a symlink and never truncating through a foreign link (#4098).

    Returns a writable fd for a REGULAR, singly-linked file, or None (fail
    closed). The open is `O_WRONLY|O_CREAT|O_NOFOLLOW|O_NONBLOCK` and
    deliberately does NOT carry `O_TRUNC`: truncation happens only AFTER the
    `fstat` gate, because the truncation itself is the primitive. A hardlink
    planted at the marker name is a REGULAR file that would pass an
    `O_TRUNC` open and truncate a file outside the candidate dir; the
    `st_nlink == 1` gate refuses it (the attacker's link, and only that link,
    is then removed with a `dir_fd`-anchored `unlink`). A stale marker left by
    this module is singly-linked and is truncated in place via `ftruncate`.

    A planted symlink is REFUSED (`ELOOP` — `O_NOFOLLOW` protects the
    basename); a FIFO cannot block (`O_NONBLOCK`); a directory is `EISDIR`.
    Anything the open lands on that fails the gate is removed with the
    `dir_fd`-anchored `unlink` (which never follows a trailing symlink) and
    retried once; a re-plant in that window, or an entry that cannot be
    unlinked (a directory occupant, an unwritable dir), fails closed.

    The `dir_fd` pins the parent, so the write cannot be redirected by a
    swapped parent directory either — `O_NOFOLLOW` alone protects only the
    basename (CVE-2018-6954 is the precedent for skipping `openat`).

    NOTE: this is strictly TIGHTENING, not "no semantic change". A candidate dir
    whose marker is ABSENT — or whose occupant must be `unlink`ed first — must be
    writable, so a readable-but-not-writable or unreadable dir now abandons the
    record instead of writing through it. A STALE REGULAR `nlink == 1` marker in
    such a dir is still overwritten (`O_CREAT` on an existing owned file needs no
    write permission on the directory). Fail-closed, and unreachable for
    redislite/mkdtemp dirs.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    for _attempt in (0, 1):
        try:
            fd = os.open(REAPER_OWNED_MARKER, flags, 0o600, dir_fd=dir_fd)
        except OSError:
            fd = None
        if fd is not None:
            try:
                st = os.fstat(fd)
                usable = stat.S_ISREG(st.st_mode) and st.st_nlink == 1
            except OSError:
                usable = False
            if usable:
                # Truncate only now, through the verified fd. A short write is
                # impossible for a 14-byte payload on a regular file, but the
                # return is checked rather than trusted.
                try:
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                except OSError:
                    os.close(fd)
                    return None
                return fd
            os.close(fd)
        # Occupied by something this must neither follow nor keep: a symlink
        # (ELOOP), a FIFO (opened non-blocking above), a directory (EISDIR),
        # or a HARDLINK (regular but nlink > 1).
        try:
            os.unlink(REAPER_OWNED_MARKER, dir_fd=dir_fd)
        except OSError:
            return None
    return None


def _kill(pid: int, sigterm_timeout: float) -> None:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + sigterm_timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.2)
    try:  # noqa: SIM105
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _cleanup_tempdir(dbdir: str | None) -> bool:
    """Guarded rmtree of a candidate tempdir.

    #4136: this is the single choke point for every rmtree the reaper
    performs, so the PROVENANCE ownership guard lives here as well as at
    each caller — a foreign-owned path, or one swapped for a symlink, is
    refused LOUDLY and never handed to `shutil.rmtree`. Returns True when
    the path was handed to rmtree (removal may still have partially
    failed — that converges next sweep), False when the guard refused it.
    """
    if not dbdir:
        return False
    if not _dir_owned_by_euid(dbdir):
        logger.warning(
            "refusing to remove %r: not a directory owned by euid %d "
            "(owner %s) — provenance guard (#4136)", dbdir, os.geteuid(),
            _dir_owner_of(dbdir))
        return False
    try:
        shutil.rmtree(dbdir, ignore_errors=True)
    except OSError:
        logger.warning("could not remove tempdir %s", dbdir)
    return True


# ── CLI + singleton lock + timeout (plan Task 3) ────────────────────

_LOCK_PATH = os.path.join(
    # #1658: the lock must be TEMPDIR-scoped, not HOME-scoped. The sweep
    # target is tempfile.gettempdir() (machine-global on Linux) — a per-HOME
    # lock means two sweepers with different $HOME (parallel agents/users/
    # containers on a shared box) each flock a DIFFERENT inode and both run
    # overlapping sweeps, reaping each other's live sockets. Same tempdir root
    # as ACTIVE_SUITES_DIR above; see the #4098 note below for why the lock DIR
    # diverges from that sibling's `<tempdir>/.tortoise`.
    #
    # #4098: the lock DIR is uid-scoped (`.tortoise-reaper-<euid>`). On a
    # shared `/tmp` any local uid can pre-create a fixed name, and the
    # ownership gate below then fails closed forever — a permanent,
    # zero-privilege denial of the victim's reaper. Because the tmpdir is
    # ours on macOS and the file is 0700, the pre-existing
    # `<tempdir>/.tortoise` is left to ACTIVE_SUITES_DIR.
    os.path.realpath(tempfile.gettempdir()),
    f".tortoise-reaper-{os.geteuid()}", ".reaper.lock")
TIMEOUT_DEFAULT = 120


class _ReaperLock:
    """fcntl-based exclusive lock; auto-released on process exit (incl. SIGKILL)."""

    def __init__(self, path: str = _LOCK_PATH):
        self.path = path
        self._fh = None

    def _close_fh_quietly(self) -> None:
        # #4098 review: `close()` can itself raise (EINTR/EIO, plausible
        # right after a failed truncate/flush on a struggling filesystem).
        # Unguarded, it would REPLACE the in-flight exception and escape
        # `acquire()` — turning a fail-closed refusal into a startup crash.
        if self._fh is not None:
            try:  # noqa: SIM105
                self._fh.close()
            except OSError:
                pass
        self._fh = None

    def _refuse(self, reason: str, foreign_owned: bool = False) -> bool:
        # #4098 review: close FIRST — a refusal abandons an open fh (and, on
        # the write-failure path, a successfully-taken flock), so leaving it
        # to refcounting is a real fd-lifetime change. `_close_fh_quietly`
        # is null-safe and idempotent, so the non-regular path's own call
        # cannot double-close.
        self._close_fh_quietly()
        # #4098 review: EVERY refusal must be LOUD. A silent `return False`
        # is indistinguishable from "another sweeper holds the lock", and on
        # a shared `/tmp` this path is attacker-triggerable and PERMANENT: a
        # foreign uid can pre-create our uid-scoped name as a directory, a
        # symlink to one, or a plain file, and the 1777 sticky bit stops us
        # removing it — so the reaper would stop sweeping until root
        # intervenes. Say so, and do not prescribe an action this uid cannot
        # perform.
        logger.warning(
            "reaper lock unavailable (%s) at %s — refusing to lock; %s",
            reason, self.path,
            # #4098 review: attribute ownership ONLY where it was established.
            # "cannot wrap the lock fd" / "not a regular file" are reachable
            # only AFTER `st_uid == geteuid` passed on a 0700 dir, so those
            # artifacts can only be ours — blaming a foreign uid there would
            # be a false diagnosis of our own stale FIFO or fd failure.
            ("this path is owned by another uid and can only be removed by "
             "its owner or root, so sweeps are skipped until then")
            if foreign_owned else
            ("this uid cannot clear the cause, so sweeps are skipped until "
             "it is fixed"))
        self._fh = None
        return False

    def acquire(self) -> bool:
        import fcntl
        # #4098 (CWE-377): the lock lives in the SHARED tempdir
        # (`<tempdir>/.tortoise`, 1777 on Linux), so its open is the same
        # symlink sink as the marker write — a plain `open(path, "a")`
        # TRUNCATED an attacker-chosen file the reaper's uid can write when
        # the lock name was planted as a symlink (and a FIFO blocked startup
        # before the SIGALRM watchdog was armed). Never follow a link and
        # never open a non-regular file; the sibling `index_lock.py` #280
        # fix is the pattern (dir 0700, O_NOFOLLOW, truncate through the fd).
        lock_dir = os.path.dirname(self.path)
        try:
            os.makedirs(lock_dir, mode=0o700, exist_ok=True)
            dir_fd = os.open(lock_dir,
                             os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError:
            return self._refuse("cannot open the lock directory")
        try:
            # #4098 review: a PRE-EXISTING dir may have been created by another
            # local uid (any local uid can `mkdir /tmp/.tortoise-reaper-<uid>`),
            # and `fchmod` tightens the mode without changing ownership — the
            # owner can chmod back and unlink/recreate the lock file, which
            # would break the singleton invariant (two reapers on two inodes).
            # Require the dir to be OURS; otherwise fail closed LOUDLY — the
            # uid-scoped name means this needs a deliberate, targeted
            # pre-creation, not the ordinary shared-`/tmp` case.
            #
            # #4098 review: `acquire()`'s contract is "return False on every
            # failure, never raise" — the invariant `_close_fh_quietly`
            # exists to preserve. An unguarded `fstat` here was the one
            # remaining violation (a filesystem-level EIO reaches it).
            try:
                dir_uid = os.fstat(dir_fd).st_uid
            except OSError:
                return self._refuse("cannot stat the lock directory")
            if dir_uid != os.geteuid():
                return self._refuse(
                    "the lock directory is not owned by this uid",
                    foreign_owned=True)
            # Best-effort tighten of a PRE-EXISTING dir. Done through the fd:
            # chmod(2) FOLLOWS a symlink and Linux has no lchmod, so a path
            # chmod would run before the O_NOFOLLOW gate and let a planted
            # `.tortoise` symlink redirect the mode change (#4098 review).
            try:  # noqa: SIM105
                os.fchmod(dir_fd, 0o700)
            except OSError:
                pass
            try:
                fd = os.open(os.path.basename(self.path),
                             os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                             dir_fd=dir_fd)
            except OSError:
                return self._refuse("cannot open the lock file")
            try:
                self._fh = os.fdopen(fd, "r+")
            except Exception:
                # os.fdopen does not take ownership on failure — close the
                # raw fd itself, or it leaks. Guard the BROAD case (not just
                # OSError) so a non-OSError from fdopen cannot leak the fd.
                try:  # noqa: SIM105
                    os.close(fd)
                except OSError:
                    pass
                return self._refuse("cannot wrap the lock fd")
        finally:
            try:  # noqa: SIM105
                os.close(dir_fd)
            except OSError:
                pass
        try:
            is_regular = stat.S_ISREG(os.fstat(self._fh.fileno()).st_mode)
        except OSError:
            is_regular = False
        if not is_regular:
            # A FIFO opens fine O_RDWR without blocking — never treat it as
            # the lock. Closed exactly once, here; an OSError from close must
            # not turn a fail-closed refusal into an exception.
            self._close_fh_quietly()
            return self._refuse("the lock path is not a regular file")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # #4098 review: split CONTENTION from FAILURE. `EWOULDBLOCK` is
            # the ordinary "another sweeper holds the lock" exit and must
            # stay SILENT (a warning here would fire on every concurrent
            # run). Every OTHER errno — EINTR/EIO from flock, or any failure
            # later in this block — is a real fault that must be LOUD, or it
            # is indistinguishable from contention.
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                self._close_fh_quietly()
                return False
            return self._refuse(f"cannot take the lock: {exc}")
        try:
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
        except OSError as exc:
            # A full/read-only tempfs (ENOSPC/EDQUOT/EIO) reaches here.
            return self._refuse(f"cannot write the lock file: {exc}")
        return True

    def release(self) -> None:
        import fcntl
        if self._fh:
            try:  # noqa: SIM105
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            # #4098 review: guarded, like every other close. `close()` can
            # raise (EINTR/EIO) and `release()` runs from `main()`'s finally
            # while `_run_sweep`'s exception may be in flight — a bare close
            # would replace it and escape main() as an uncaught traceback.
            self._close_fh_quietly()


def _parse_timeout(cli_value: str | None) -> int:
    """Timeout resolution: CLI --timeout > TORTOISE_REAPER_TIMEOUT env > 120.

    A resolved value < 1 is refused and falls back to the default:
    `signal.alarm(0)` CANCELS the alarm, so `--timeout 0` would run the sweep
    unbounded while holding `_ReaperLock` — every later fire then exits
    `already running`. (The installer guards its own `REAPER_TIMEOUT`, but the
    documented CLI/env flag must be safe on its own.)
    """
    if cli_value is not None:
        try:
            value = int(float(cli_value))
        except ValueError:
            logger.warning("invalid --timeout %r — using default", cli_value)
        else:
            if value >= 1:
                return value
            logger.warning("--timeout %r must be >= 1 — using default",
                           cli_value)
    env = os.environ.get("TORTOISE_REAPER_TIMEOUT", "")
    if env:
        try:
            value = int(float(env))
        except ValueError:
            logger.warning(
                "TORTOISE_REAPER_TIMEOUT=%r invalid — using default", env)
        else:
            if value >= 1:
                return value
            logger.warning(
                "TORTOISE_REAPER_TIMEOUT=%r must be >= 1 — using default", env)
    return TIMEOUT_DEFAULT


# #1231: stale per-session index-lock pid files. SessionIndexLock.release()
# now removes its pid file on graceful shutdown (index_lock.py); files that
# survive are crash leftovers (flock is kernel-released on death) and are
# swept here. Age-guarded like the socket walk's boot cooldown: files
# younger than this are never touched (a just-created lock whose holder is
# mid-acquire must not be deleted).
INDEX_PID_MIN_AGE_DEFAULT = 30


def _index_lock_dir() -> str:
    """Resolve the per-session index-lock dir (mirrors index_lock.lock_path_for)."""
    return os.environ.get("TORTOISE_INDEX_LOCK_DIR", "") or os.path.join(
        os.path.expanduser("~"), ".tortoise")


def sweep_stale_index_pid_files(lock_dir: str | None = None,
                                dry_run: bool = False,
                                min_age: int = INDEX_PID_MIN_AGE_DEFAULT) -> list[str]:
    """Remove stale ``index-*.pid`` lock files (#1231 T3).

    A pid file is stale when its recorded holder is dead: the kernel
    releases the flock on holder death, so a lock whose flock can be taken
    AND whose recorded pid is gone is a crash leftover. Removal goes
    through ``SessionIndexLock.force_release()`` — the TOCTOU-hardened
    unlink (takes the flock first, so a live holder or mid-acquire
    contender is never evicted; refuses symlinks; verifies the locked
    inode is the path; unlinks while holding). Age-guarded like the socket
    walk: files younger than ``min_age`` are never touched.

    Returns the list of removed (or would-remove in dry-run) file paths.
    """
    from .index_lock import SessionIndexLock

    if lock_dir is None:
        lock_dir = _index_lock_dir()
    removed: list[str] = []
    try:
        entries = sorted(os.scandir(lock_dir), key=lambda e: e.name)
    except OSError:
        return removed
    now = time.time()
    for entry in entries:
        if not entry.is_file():
            continue
        if not (entry.name.startswith("index-") and entry.name.endswith(".pid")):
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < min_age:
            continue  # boot-cooldown guard, mirrors the socket walk
        session_id = entry.name[len("index-"):-len(".pid")]
        lock = SessionIndexLock(session_id, lock_dir)
        if dry_run:
            # Non-authoritative would-remove: staleness by attribution only
            # (no flock probe) — never mutates in dry-run mode.
            if lock.held_by().get("stale"):
                removed.append(entry.path)
            continue
        try:
            if lock.force_release():
                removed.append(entry.path)
                logger.warning("removed stale index lock file %s", entry.path)
        except Exception as exc:  # per-file isolation
            logger.warning("pid-file sweep failed for %s: %s", entry.path, exc)
    return removed


def _sweep_quarantine_dirs(dry_run: bool = False,
                           budget: int = STALE_SWEEP_BUDGET) -> list[str]:
    """Re-probe *.reaper-stale-* quarantine leftovers and remove dead ones.

    #1383 convergence: a partial-rmtree or respawn-during-rename leaves a
    renamed dir. discover() pass 2 SKIPS quarantine dirs (reaper-owned —
    plan-review P1), so this pass is their only handler: re-probe the
    moved socket (a server moved with its dir retains its socket inode, so
    the probe is authoritative) and remove only dead ones. Same budget
    CONSTANT as reap()'s stale branch but a SEPARATE counter — one sweep
    can remove up to 2xSTALE_SWEEP_BUDGET (plan-review cycle 2). Scanned
    via the shared depth-1 `os.scandir` primitive (#4068, same primitive as
    discover pass 2 — the scan is NOT namespace-scoped because a quarantine
    rename preserves the dir's ephemeral name anyway, but the scan itself
    is detection-oriented and every removal below is re-verified by
    `_is_ephemeral_dir`); symlinked entries are skipped (mirror discover
    pass 2). A truncated scan logs a WARNING and this pass converges on the
    next sweep.
    """
    tmpdir = _real_gettempdir()
    scan = _iter_candidate_dirs(
        tmpdir,
        predicate=lambda name: STALE_QUARANTINE_SUFFIX in name,
        deadline=time.monotonic() + SOCKET_WALK_TIMEOUT)
    removed = []
    for q in scan.dirs:
        if len(removed) >= budget:
            break
        if os.path.islink(q) or not os.path.isdir(q):
            continue  # symlink safety mirrors discover pass 2 (cycle 2)
        # #1383 security review (Issue 3): only rmtree dirs carrying the
        # reaper-owned marker — a same-suffix foreign dir (another tool's
        # temp naming, a planted decoy) must never be touched. The marker
        # is written at rename-aside time in the stale action.
        if not os.path.exists(os.path.join(q, REAPER_OWNED_MARKER)):
            continue
        # #4136 provenance guard: the quarantine sweep's rmtree may only
        # touch a directory owned by our euid. A foreign uid can plant a
        # same-suffix dir plus a marker (the marker test is a plain
        # `exists`, which follows a symlink), so `_is_ephemeral_dir`'s name
        # scope is not provenance. Checked at the action, no-follow.
        if not _dir_owned_by_euid(q):
            logger.warning(
                "quarantined dir %r is not owned by euid %d (owner %s) — "
                "provenance guard (#4136), leaving", q, os.geteuid(),
                _dir_owner_of(q))
            continue
        if not _is_ephemeral_dir(os.path.realpath(q),
                                 os.path.realpath(tempfile.gettempdir())):
            continue  # containment re-verify (defense in depth)
        qsock = os.path.join(q, SOCKET_MARKER)
        # Guard-8 equivalent (cycle-3 P1): a LIVE backlog-full server
        # answers ECONNREFUSED ('dead') — the moved pidfile is the
        # discriminator. A guard-8-preserved quarantine left by reap() in
        # the SAME sweep must never be rmtree'd here.
        try:
            qpid = int(Path(os.path.join(q, PIDFILE_MARKER)).read_text().strip())
        except (OSError, ValueError):
            qpid = None
        if qpid is not None and _pid_effectively_alive(qpid):
            logger.warning("quarantined dir pidfile live redis (%s), "
                           "leaving: %s", qpid, q)
            continue
        if not os.path.exists(qsock):
            # Partial-rmtree shell (SIGALRM interrupt deleted the socket
            # first): only a server that unlinked its socket leaves a
            # socket-less quarantine — and an unlinked socket serves
            # nobody. Remove once aged (mtime guard) so the shell
            # converges instead of leaking (plan-review P1).
            try:
                qage = time.time() - os.stat(q).st_mtime
            except OSError:
                continue
            if qage < STALE_SOCKET_MIN_AGE_DEFAULT:
                continue
            if dry_run:
                logger.warning("[DRY-RUN] would remove quarantined dir %s", q)
                removed.append(q)
                continue
            _cleanup_tempdir(q)
            removed.append(q)
            logger.warning("removed socket-less quarantined dir %s", q)
            continue
        if _probe_socket_any(qsock, timeout=PROBE_SOCKET_TIMEOUT) != "dead":
            logger.warning("quarantined dir socket live, leaving: %s", q)
            continue
        if dry_run:
            logger.warning("[DRY-RUN] would remove quarantined dir %s", q)
            removed.append(q)
            continue
        _cleanup_tempdir(q)
        removed.append(q)
        logger.warning("removed quarantined dir %s", q)
    out = _ScanAwareList(removed)
    out.complete = scan.complete
    return out


def _run_sweep(dry_run: bool, batch_size: int | None, only_safe: bool = False,
               jobs: int = 8, kill_pacing: float = KILL_PACING_DEFAULT,
               sweep_pid_files: bool = True,
               sigterm_timeout: float = 10.0,
               deadline: float | None = None,
               full_scan: bool = False) -> list[dict]:
    """Discover + classify + reap; return acted-upon records.

    deadline: optional monotonic-clock cutoff threaded into reap() — a
    bounded sweep aborts mid-call once hit (the pre-probe cache is also
    skipped), so the suite-end sweep can never run past pytest-timeout no
    matter how large the discovered backlog is.

    jobs>1 parallelizes the per-candidate CLIENT LIST probes (a
    parallelizable cost at hundreds of leaked servers — issue #1005); kills
    stay serial
    with pacing. sigterm_timeout threads into reap()/_kill(): the suite-end
    sweep (conftest) lowers it to 3.0 so a server ignoring SIGTERM gets
    SIGKILL quickly — the default 10s wait × many servers compounds past
    pytest-timeout under CI load (epic #1647 PR #1684 CI-fix; the param was
    missing from _run_sweep's signature, so the conftest kwarg raised
    TypeError and the end-sweep silently no-oped — observed as the 104-
    orphan leak on the tier-2 leg).

    NOTE: the reaper singleton lock is held by main() (CLI); direct callers
    (tests, conftest session hygiene) run unlocked — pre-existing contract,
    unchanged by #1383.

    #4068: `full_scan` is forwarded unconditionally; completeness is read
    # FAIL-CLOSED through `_as_scan_aware`, so an unknown discovery result
    # (a monkeypatched seam returning a plain list) can never read as a
    # finished scan. The returned list is a `_ScanAwareList` carrying
    # `.complete` (False = at least one bounded scan returned a partial set).
    """
    # #4438 review P2: a non-positive `--jobs` (0 or -1) would reach reap()'s
    # `ThreadPoolExecutor(min(jobs, len(records)))` as 0 and crash with
    # `max_workers must be greater than 0`. Clamp before anything uses it.
    if jobs < 1:
        logger.warning("jobs=%r must be >= 1 — using 1", jobs)
        jobs = 1
    records = _as_scan_aware(discover(jobs=jobs, full_scan=full_scan))
    discovery_complete = records.complete
    # #1383: reapable classes are candidate (live orphan -> kill) and
    # stale_socket (dead-pid leftover dir -> guarded rmtree). Phase 1
    # resolves stale-pid records before any action.
    # #1642 FIX 3: annotate live 0-client candidates with orphan-confirmed
    # state (persisted (pid, start) observation — FIX 5) BEFORE any action,
    # so only_safe and the path_based gate can distinguish genuine orphans
    # from a concurrent suite's between-tests idle server.
    _mark_orphan_confirmation(records)
    # #1383: reapable classes are candidate (live orphan -> kill) and
    # stale_socket (dead-pid leftover dir -> guarded rmtree). Phase 1
    # resolves stale-pid records before any action.
    reapables = [r for r in records
                 if r["classification"] in ("candidate", "stale_socket")]
    resolved = [phase1_probe(r) for r in reapables]
    acted = reap(resolved, dry_run=dry_run, batch_size=batch_size,
                 kill_pacing=kill_pacing, only_safe=only_safe,
                 sigterm_timeout=sigterm_timeout, jobs=jobs,
                 deadline=deadline)
    # #1383: quarantine convergence (partial-rmtree/respawn leftovers)
    try:
        quarantine = _sweep_quarantine_dirs(dry_run=dry_run)
        for q in quarantine:
            acted.append({"pid": None, "quarantine_dir": q,
                          "classification": "stale_quarantine"})
        # #4068: the quarantine scan is a SECOND bounded scan on the same
        # sweep — its truncation must not be hidden behind pass 2's flag.
        discovery_complete = discovery_complete and getattr(
            quarantine, "complete", False)
    except Exception as exc:  # never fail the sweep over hygiene
        logger.warning("quarantine sweep failed: %s", exc)
    # #1231 T3: stale per-session index-lock pid files (crash leftovers).
    # Runs after the socket sweep under the same singleton lock; age-guarded
    # and TOCTOU-hardened via SessionIndexLock.force_release().
    if sweep_pid_files:
        try:
            removed = sweep_stale_index_pid_files(dry_run=dry_run)
            for path in removed:
                acted.append({"pid": None, "pid_file": path,
                              "classification": "stale_pid_file"})
        except Exception as exc:  # never fail the sweep over pid hygiene
            logger.warning("index-pid sweep failed: %s", exc)
    out = _ScanAwareList(acted)
    out.complete = discovery_complete
    return out


# #4740: the field set of the session-end hygiene report the CI orphan gate
# (`.github/scripts/orphan-bound.sh`) consumes. It lives here, with the report
# builder, as the single source of truth: `tests/conftest.py` imports both
# instead of redeclaring, and the orphan-bound harness reads this assignment
# from this file's source. Order is the order the report is built in.
_HYGIENE_REPORT_FIELDS = ("reaped", "cleared", "left", "before")


def _hygiene_report(reaped, cleared, left, before) -> dict:
    """Build the session-end hygiene report the CI orphan gate consumes (#4740).

    ``cleared`` is the sweep's OWN outcome (see :func:`sweep_until_cleared`),
    threaded through verbatim and never synthesised here. ``cleared`` is a
    diagnostic flag that does not decide the gate's verdict at any measured
    count: at every count it reports whether the sweep's time budget sufficed
    — a function of runner load — not the residue.
    Keeping the construction out of ``_sweep`` also leaves no local report
    literal there for a dead branch or a subscript store to bypass (the
    round-5 pin's hole).
    """
    values = {
        "reaped": reaped,
        "cleared": cleared,
        "left": left,
        "before": before,
    }
    return {f: values[f] for f in _HYGIENE_REPORT_FIELDS}


def sweep_until_cleared(run_one, deadline, clock=time.monotonic):
    """Drive discover->reap iterations until the backlog clears or the
    deadline passes; return ``(total_acted, cleared)``.

    ``cleared`` is the sweep's own budget/stop-condition claim, carried into
    the report for diagnosis — not a field the CI orphan gate decides its
    verdict on. It is True ONLY when ALL hold:

    * the loop stopped on an iteration that acted on NOTHING (the backlog is
      empty — the intended stop), not on the deadline;
    * the deadline had NOT passed when that empty iteration returned, so the
      empty result is a real measurement rather than ``reap()``'s
      first-record deadline break returning a partial, possibly empty list;
    * that iteration's discovery scan was COMPLETE (``.complete`` on the
      scan-aware list — fail-closed False for an unknown/truncated scan). A
      partial scan that acted on nothing proves nothing.

    #4740 review 4: the previous ``cleared = not acted`` treated an
    already-expired-deadline abort (``reap()`` returns ``[]`` because its
    first record hit the deadline) as a CLEARED backlog, so the gate greened
    a residue whose entire backlog had never been examined. A spent deadline
    is not proof the backlog is clear.
    """
    total = 0
    cleared = False
    acted = []
    while True:
        acted = run_one()
        total += len(acted)
        if not acted:
            # The one stop that can mean "cleared" — but only if the budget
            # remained: an empty list returned BECAUSE the deadline had
            # already expired is an unexamined backlog, not a clear one.
            cleared = clock() < deadline
            break
        if clock() >= deadline:
            # Budget exhausted while servers were still being acted on: the
            # residue is arbitrary, so `cleared` stays False.
            break
    # A partial discovery scan is never "cleared", whatever it acted on.
    cleared = cleared and bool(getattr(acted, "complete", False))
    return total, cleared


def live_embedded_server_count() -> int | None:
    """Live embedded redis-server count, or None when the probe itself failed.

    #4740: the number the CI orphan gate binds its bound to. `None` (the probe
    failed) is NOT 0 (measured none) — the gate must name an unmeasured
    residue rather than read a timeout/missing-pgrep as "nothing left". This
    is a module-level function, not a closure in `tests/conftest.py`, so it is
    unit-testable by monkeypatching `_pgrep_redis_servers_or_none`.
    """
    probe = _pgrep_redis_servers_or_none()
    return None if probe is None else len(probe)


def build_end_sweep_report(run_one, deadline, probe, clock=time.monotonic) -> dict:
    """Compose the session-end hygiene report the CI orphan gate consumes (#4740).

    Extracted from `tests/conftest.py`'s `_sweep` so the COMPOSITION — the
    pre-sweep reading, the sweep, the post-sweep reading, and their arrangement
    into the report — is behaviourally testable (`tests/test_reaper.py` drives
    this function directly). `probe` is called twice: the first reading is
    `before`, the second is `left`; `cleared` is threaded verbatim from
    `sweep_until_cleared`, because `cleared` is a diagnostic flag that does
    not decide the gate's verdict at any measured count.
    """
    before = probe()
    reaped, cleared = sweep_until_cleared(run_one, deadline, clock)
    left = probe()
    return _hygiene_report(reaped, cleared, left, before)


def _zero_client_state_read() -> dict:
    """Read the persisted zero-client observation state (best-effort).

    Keyed by realpath socket path; entry = {pid, start, first_seen}.
    Returns {} on any read error — the state is an accelerator for
    orphan confirmation, never a correctness dependency.
    """
    try:
        return json.loads(Path(ZERO_CLIENT_STATE_PATH).read_text())
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _zero_client_state_write(state: dict) -> None:
    """Persist the zero-client state atomically (tmp file + os.replace).
    Best-effort: a write failure never fails the sweep.
    """
    try:
        os.makedirs(os.path.dirname(ZERO_CLIENT_STATE_PATH), exist_ok=True)
        tmp = ZERO_CLIENT_STATE_PATH + f".{os.getpid()}.tmp"
        Path(tmp).write_text(json.dumps(state, indent=2))
        os.replace(tmp, ZERO_CLIENT_STATE_PATH)
    except OSError:
        logger.warning("could not persist zero-client state")


def _owner_pid_alive(pid: int) -> bool:
    """Fail-closed liveness for an OWNER pid (#3599 review).

    `_pid_alive` returns False on EPERM as well as ESRCH, because for its
    original callers ("is this pid a reapable redis-server?") not-being-able-
    to-signal is the SAFE direction. For an owner record the question is
    inverted — False means "this owner is dead, so the server is an orphan"
    — and an owner we merely cannot signal would licence a kill. So a pid
    that exists but is not ours counts LIVE here, and only a pid this
    process can positively prove gone counts dead.
    """
    if _pid_alive(pid):
        return True
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # exists, other uid/owner — fail closed
    except (OSError, OverflowError, ValueError):
        return False
    # kill(0) succeeded but _pid_alive said no -> zombie (its fds are gone).
    return False


def _owner_lock_held(socket_path: str) -> bool | None:
    """Does a LIVE owner hold the server's owner lock? (#4577)

    The kernel-fact version of the #1557 "a live owner must never be killed"
    guarantee. The writer (`embedded_lifecycle.record_owner`) holds a SHARED
    ``flock`` on ``<socket_dir>/.tortoise-owners/.lock`` for the process's
    lifetime; the kernel releases it when the holder dies, so an EXCLUSIVE
    non-blocking lock that FAILS to acquire proves a live owner with no pid
    or start-time inference at all (no recycled-pid, no unreadable-`ps`,
    no copied-record failure class).

    Returns:
      - ``True``: the probe was refused (``BlockingIOError`` / ``EAGAIN``)
        because a live owner holds the shared lock. The caller must NEVER
        confirm or kill that server.
      - ``False``: the lock was acquired, so no live owner holds it. This is
        NOT by itself authorization to kill — the caller still runs the
        existing evidence chain (0-client double probe, window/`unattributed`
        rules, euid provenance, `_owner_records`).
      - ``None``: the lock file is missing or unreadable (an owner built
        before this change, a symlink planted at ``.lock``, an I/O error) —
        UNKNOWN, so the caller falls through to today's ``_owner_records``
        path. Backward compatibility: "no lock file" is never "orphan".

    ``O_NOFOLLOW`` so a symlink at ``.lock`` is never followed (#4098
    discipline); a planted symlink therefore reads UNKNOWN rather than
    resolving onto (and taking a lock on) whatever it points at. The probe
    releases the exclusive lock it takes before returning, so it can never
    block a later owner from claiming the server. Never raises.
    """
    import fcntl

    if not socket_path:
        return None  # defensive: this function's contract is "never raises"
    path = os.path.join(os.path.dirname(socket_path), OWNERS_DIRNAME,
                        OWNER_LOCK_NAME)
    # #4577 review, #4098 discipline (same as `_lock_holder_pid`): never
    # BLOCK on a planted non-regular file. `open(FIFO, O_RDONLY)` with no
    # writer parks FOREVER, and this probe runs for every candidate in
    # `_mark_orphan_confirmation` — including the conftest end-sweep, which
    # arms NO SIGALRM watchdog — so one planted FIFO would hang the sweep
    # and pin every orphan on the host. `O_NONBLOCK` makes the open
    # non-blocking; the `S_ISREG` check rejects anything that is not the
    # regular file this module writes.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK
                     | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None  # missing / symlink / unreadable -> UNKNOWN, fall back
    try:
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None  # planted FIFO/device -> UNKNOWN (#4098)
        except OSError:
            return None
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # EWOULDBLOCK is EAGAIN on Linux/macOS; both spellings are
            # checked so the verdict cannot flip with an errno alias. Any
            # OTHER OSError means we cannot trust the probe -> UNKNOWN.
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return True  # a live owner holds the shared lock
            return None
        # Acquired: no live owner holds the server. Release at once — the
        # probe must never leave a server locked against a future owner.
        try:  # noqa: SIM105
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:  # noqa: SIM105
            os.close(fd)
        except OSError:
            pass


def _owner_record_dir_present(socket_dir: str) -> bool:
    """True when `socket_dir` carries tortoise's owner-record instrument
    (#3599) with at least one recognisable record file.

    A CHEAP attribution test (one `listdir`, no `ps`). #3767 uses it for two
    things: the registry-less ownership claim (`_has_ownership_claim`), and
    the record's `unattributed` flag — a server with no tortoise instrument
    cannot be attributed to us, so `reap()` requires the #1557/#1642 FIX 3
    orphan-confirmation window for it in EVERY mode, EXCEPT a `dir_missing`
    record in a FULL sweep (#4546: its registry data dir is already gone, so
    no on-disk user data remains; the `path_based` arm has no such exemption,
    and under `--only-safe` the earlier live-pid gate still skips first).

    Deliberately NOT `_owner_records`, whose per-record liveness resolution
    shells out to `ps`; the orphan VERDICT still comes from `_owner_records`
    (`_mark_orphan_confirmation` / `reap`).

    Recognition matches `_owner_records`: a dotted name is ignored and an
    unparsable/non-positive pid prefix is a foreign file. An empty dir reads
    False, mirroring `_owner_records`' "total == 0 is no evidence at all"
    fail-closed rule. Fail closed on OSError. (The dotted-name skip is
    intent-documenting, NOT load-bearing: no dotted name can pass the
    positive-pid parse anyway, since `int(".lock".partition("-")[0])` raises
    ValueError. It is stated here so the rule reads the same as
    `_owner_records`'.)

    RESIDUAL (#3767 review P2, accepted + documented): a tortoise server
    whose owner closed GRACEFULLY while the daemon survived loses its
    instrument (`forget_owner` removes the record and rmdirs the dir, #3599),
    so it reads `unattributed` and reap() no longer fast-kills it in a full
    sweep — EXCEPT a `dir_missing` record, which #4546 exempts — otherwise it
    converges only through the #1557/#1642 FIX 3 confirmation window. That is
    the deliberate fail-CLOSED direction: an instrument-less
    server is byte-for-byte indistinguishable from a foreign same-uid
    application's (`_has_ownership_claim`), and the whole point of #3767 is
    that we may not kill what we cannot attribute. Distinguishing "never
    instrumented" from "instrument withdrawn" would need a tombstone the
    writer does not leave; not doing so is the accepted cost.
    """
    try:
        names = os.listdir(os.path.join(socket_dir, OWNERS_DIRNAME))
    except OSError:
        return False
    for n in names:
        if n.startswith("."):
            continue
        try:
            pid = int(n.partition("-")[0])
        except ValueError:
            continue
        if pid > 0:
            return True
    return False


def _owner_records(socket_path: str) -> tuple[int, int] | None:
    """(live_owners, total_owners) recorded for the server at ``socket_path``.

    #3599: the per-server orphan discriminator. None when no owner-record
    dir exists (an uninstrumented spawn — the caller must fall back to the
    global suite-marker gate, fail closed).

    Only ever used to PROVE orphanhood, never to assume it: a record whose
    (pid, start) identity cannot be verified (an unreadable start time, the
    `unknown` stamp the writer emits when `ps` was unavailable, or a
    non-finite/unparsable one) counts as LIVE whenever its pid is alive —
    while a record whose pid is provably DEAD is a stale record and does
    not count, even if its start is unreadable (otherwise nothing would
    ever prune it). A file whose name is not a positive integer pid prefix
    is a foreign file and is ignored entirely; a bare `<pid>` with no start
    is treated as an unverifiable-start record. A pid we cannot signal
    counts LIVE (`_owner_pid_alive`). A shared server is safe by
    construction — every constructor writes its own record, so a co-tenant
    that attached after the creator is its own live entry and the server is
    never confirmed (the #1557 kill-a-live-server hazard).
    """
    d = os.path.join(os.path.dirname(socket_path), OWNERS_DIRNAME)
    try:
        names = os.listdir(d)
    except OSError:
        return None
    live = 0
    total = 0
    for n in names:
        if n.startswith("."):
            continue
        pid_s, _, start_s = n.partition("-")
        # Parse the pid ONCE, guarded: `str.isdigit()` is True for
        # non-decimal Unicode digits (`²`, `①`) that `int()` rejects, so
        # `isdigit()` alone is not a safe gate before an `int()` call.
        # An unparsable name is a foreign file — ignore it entirely.
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if pid <= 0:
            # #3599 review: `os.kill(0, 0)` targets the caller's OWN process
            # group and therefore SUCCEEDS, so a file named `0` would read as
            # a live owner forever and permanently defeat reaping for that
            # server (the exact accumulation #3599 fixes). A real owner pid
            # is always >= 1 (`record_owner` writes `os.getpid()`).
            continue
        total += 1
        # Resolve the stamp to a finite float, or None when it is
        # UNVERIFIABLE — the 'unknown' sentinel, an empty stamp (a bare
        # `<pid>` filename), an unparsable one, or a non-finite one.
        # `float()` happily accepts 'nan'/'inf'/'1e400', and
        # `abs(current - nan) < 2.0` is False, so a non-finite stamp would
        # otherwise take the RECYCLED-PID path and count a LIVE owner as
        # dead — a false orphan verdict, i.e. the reaper killing a live
        # suite's server (#3599 review, fail-open, reproduced).
        start: float | None = None
        if start_s and start_s != "unknown":
            try:
                start = float(start_s)
            except ValueError:
                start = None
            else:
                if not math.isfinite(start):
                    start = None
        if start is None:
            # #3599 review cycle 2 + 5: only the START is unverifiable here —
            # the pid is known. Gating this on liveness (rather than counting
            # it live unconditionally) is what keeps a SIGKILLed owner's
            # record from becoming immortal: nothing prunes owner files
            # (`forget_owner` only matches its own pid prefix), so an
            # immortal record pins `owners[0] > 0` forever and the server
            # can never be orphan-confirmed by the only_safe cron or the
            # conftest end-sweep. That population IS #3599 (killed lanes on
            # a loaded host, where a `ps` timeout is how an 'unknown' stamp
            # arises). A dead pid is verifiable even when its identity is
            # not:
            #   pid dead   -> dead owner (the record is stale)
            #   pid alive  -> LIVE (fail closed; a recycled pid included)
            if _owner_pid_alive(pid):
                live += 1
            continue
        # #3599 review P0: _pid_identity_matches() is the WRONG primitive
        # here — it returns False both for a recycled pid (provably dead)
        # and for an alive pid whose start time could not be read (a `ps`
        # timeout, exactly this host's loaded condition). Treating the
        # second as dead would orphan-confirm a server with a LIVE owner
        # and let the reaper kill it at its next 0-client moment
        # (#1005/#1557). Decide the cases explicitly instead:
        #   pid dead                 -> dead
        #   start unreadable (None)  -> LIVE (fail closed)
        #   start matches            -> LIVE
        #   start differs (recycled) -> dead
        if not _owner_pid_alive(pid):
            continue
        current = _process_start_time(pid)
        if current is None or abs(current - start) < 2.0:
            live += 1
    if total == 0:
        return None  # empty dir is no evidence at all -> fail closed
    return live, total


def _mark_orphan_confirmation(records: list[dict]) -> None:
    """#1642 FIX 3: decide orphanhood for LIVE 0-client candidates.

    A live detached server with 0 clients is NOT yet provably an orphan — a
    concurrent suite's between-tests idle server looks identical (#1557:
    all redislite servers daemonize to ppid=1). Orphanhood is confirmed by
    the FIRST signal that proves it, per server:
      1. #4577: no live lock HOLDER. A live owner process holds a shared BSD
         flock on `<socket_dir>/.tortoise-owners/.lock`; the kernel drops it
         when the holder dies, so "a live owner holds the server" is a FACT
         rather than a pid+start reconstruction (no recycled-pid, no
         unreadable-`ps`, no copied-record failure class). A HELD lock vetoes
         confirmation outright, independent of anything else. A lock-less
         owner (created before this change, or a symlinked/unreadable lock)
         reads UNKNOWN and falls through to signal 2 — never to "orphan".
      2. #3599: no live owner RECORD. tortoise.FalkorDB records one owner
         file per owning process inside the server's socket dir; all owners
         provably dead => orphan, independent of what other suites on the
         host are doing. This is the signal that breaks the fleet deadlock:
         the global gate below is permanently True on a host running many
         concurrent sessions, so nothing was ever confirmed and the
         only_safe cron was a no-op (#3599). Restricted to EPHEMERAL
         test-tree servers (`path_based` False).
      3. #1642 FIX 3 fallback (uninstrumented spawns — no owner records, and
         also the socket-dir-missing case): the 0-client state must have
         persisted >= ZERO_CLIENT_CONFIRM_MINUTES across sweeps AND no live
         suite markers may exist (FIX 4). A missing socket dir is NOT an
         immediate signal: a live server whose dir was unlinked still
         serves already-established connections, which a path-based CLIENT
         LIST probe cannot see (the #1557 lifecycle race).
    State is keyed by (pid, process_start_time) so a recycled pid restarts
    the window (FIX 5). Mutates records in place (`_orphan_confirmed`);
    reap() reads the flag. Never raises.
    """
    state = _zero_client_state_read()
    now = time.time()
    changed = False
    # Prune entries older than the horizon (confirmed/reaped servers are
    # never seen again — their socket dir is gone).
    stale_keys = [k for k, e in state.items()
                  if now - e.get("first_seen", 0) > ZERO_CLIENT_STATE_MAX_AGE]
    for k in stale_keys:
        del state[k]
        changed = True
    candidates = [r for r in records
                  if r.get("classification") == "candidate"
                  and r.get("socket_path") and r.get("pid")
                  and _pid_alive(r["pid"])]
    if candidates:
        # One batched ps for the confirmation checks (start + ppid) — the
        # per-sweep cache discover() populated was cleared in its finally.
        _PROC_INFO_CACHE.update(
            _batch_process_info([r["pid"] for r in candidates]))
    suites_active = bool(active_suite_tokens())
    for rec in candidates:
        cc = rec.get("client_count")
        if cc is not None and cc > 0:
            # Clients are connected -> not an orphan, clear any window.
            if rec["socket_path"] in state:
                del state[rec["socket_path"]]
                changed = True
            continue
        # #3599: PER-SERVER orphanhood, checked BEFORE the CLIENT LIST
        # fail-closed branch and before the global suite-marker gate. A
        # server with no live owner is an orphan no matter how many other
        # suites are running, and no matter whether the server is too
        # loaded (or its socket path too long) to answer a probe — keying
        # this on either of those is what made the only_safe sweeps no-ops
        # on a fleet host (527 orphans, host load 98/10 CPUs).
        #   (a) at least one LIVE owner -> never an orphan (returns early),
        #       including against the window fallback below.
        #   (b) ALL owner records provably dead -> orphan. Restricted to
        #       EPHEMERAL test-tree servers (`path_based` False) so the
        #       blast radius is the disposable leak class this issue
        #       measured; a user-data server keeps the conservative window
        #       (its db outlives the test tree — #1642 FIX 3).
        #   (c) socket DIR gone: NOT an immediate signal. It stays on the
        #       pre-existing conservative path below (window + identity +
        #       no live suite markers). A live server whose socket dir is
        #       unlinked still serves clients over already-established
        #       connections (a unix connection survives unlink and is
        #       invisible to a CLIENT LIST probe that needs the path), so
        #       treating a missing dir as instant proof of orphanhood is the
        #       #1557 test-tempdir lifecycle race that a prior review on this
        #       file rated P1 (PR #1558). `_socket_dir_missing` is also NOT
        #       `path_based`-gated, so the immediate arm would have killed
        #       user-data servers too.
        # NOTE: confirming here cannot itself kill a served server —
        # reap() still requires a verified 0-client CLIENT LIST probe
        # (before AND after) before any _kill, EXCEPT on the socketless
        # fast path (dir gone + confirmed), which is why the dir-missing
        # signal must keep its window.
        # #4577: the KERNEL-FACT liveness signal, checked FIRST. A live
        # owner holds a shared flock on the owner dir's `.lock`; the kernel
        # drops it when the process dies, so a held lock proves a live owner
        # with none of the pid+start inference's failure classes. True ->
        # never confirm (skip, and clear any window state). False/None ->
        # the record-based chain below decides, UNCHANGED: a FREE lock does
        # not by itself authorize a kill, and an owner built before this
        # change has no lock file at all (backward compatible).
        if _owner_lock_held(rec["socket_path"]) is True:
            if state.pop(rec["socket_path"], None) is not None:
                changed = True
            continue
        owners = _owner_records(rec["socket_path"])
        if owners is not None and owners[0] > 0:
            if state.pop(rec["socket_path"], None) is not None:
                changed = True
            continue
        no_live_owner = (owners is not None and owners[0] == 0
                         and not rec.get("path_based"))
        if no_live_owner:
            rec["_orphan_confirmed"] = True
            if state.pop(rec["socket_path"], None) is not None:
                changed = True
            continue
        if cc is None and not _socket_dir_missing(rec["socket_path"]):
            # #1642 FIX 3: a SOCKET-LESS live server — socket dir GONE, so
            # no client can exist and CLIENT LIST probes cannot succeed — is
            # an orphan once the confirmation window + (pid, start) identity
            # + no-live-markers hold (the missing-dir signal substitutes for
            # the 0-client probe). A probe failure with the socket dir still
            # present is a transient (loaded server) -> fail closed.
            continue  # probe failed but dir exists -> fail closed
        start = _process_start_time(rec["pid"])
        entry = state.get(rec["socket_path"])
        # #1642 FIX 5 (review P1): compare the process's CURRENT start against
        # the PERSISTED start in the state entry — a recycled pid (now a
        # different redis-server with a different start) must NOT inherit the
        # old first_seen window (it would be orphan-confirmed on its first
        # sweep and killed at 0 clients — the #1557 live-server hazard).
        identity = bool(entry and entry.get("pid") == rec["pid"]
                        and _pid_identity_matches(rec["pid"],
                                                  entry.get("start")))
        if identity and now - entry["first_seen"] \
                >= ZERO_CLIENT_CONFIRM_MINUTES * 60 and not suites_active:
            rec["_orphan_confirmed"] = True
        else:
            state[rec["socket_path"]] = {
                "pid": rec["pid"],
                "start": start,
                "first_seen": entry["first_seen"] if identity else now,
            }
            changed = True
    if changed:
        _zero_client_state_write(state)


def _socket_dir_missing(socket_path: str) -> bool:
    """True when the socket's PARENT DIR is gone (not just the socket file
    missing — the whole dir was deleted). A live server whose socket dir is
    gone cannot serve anyone: no socket path exists for a client to connect
    to. The signal substitutes for the 0-client CLIENT LIST probe in the
    orphan confirmation (#1642 FIX 3 — hundreds of socket-less orphans
    observed on the dev box: alive daemonized redis-servers whose tmp dirs
    were swept from under them)."""
    d = os.path.dirname(socket_path)
    return bool(d) and not os.path.isdir(d)


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json as _json
    import signal
    import sys

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        prog="tortoise.embedded_reaper",
        description="Reap orphaned redislite redis-server processes "
                    "(issue #176).",
    )
    parser.add_argument("--no-dry-run", action="store_true",
                        help="Actually kill orphans (default is dry-run)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Limit kills per run (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--jobs", type=int, default=8,
                        help="Parallel probe workers (default 8)")
    parser.add_argument("--only-safe", action="store_true",
                        help="Concurrent-suite-safe sweep: kill only "
                             "orphan-CONFIRMED live servers — either a "
                             "per-server all-owners-dead signal (#3599) or "
                             "the persisted 0-client window with no live "
                             "suite markers (#1642 FIX 3) — plus "
                             "stale_socket removals (#1642 FIX 3; the "
                             "scheduled-cron mode)")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    parser.add_argument("--full-scan", action="store_true",
                        help="Restore the pre-#4068 UN-SCOPED tempdir "
                             "enumeration (default is scoped to the "
                             "ephemeral namespace). It can reach nothing an "
                             "earlier release could not, and cannot widen "
                             "an rmtree (containment is re-derived at every "
                             "removal); the scheduled sweep stays scoped. "
                             "[env TORTOISE_REAPER_FULL_SCAN]")
    parser.add_argument("--timeout", type=str, default=None,
                        help=f"Sweep timeout in seconds (default "
                             f"{TIMEOUT_DEFAULT}; env TORTOISE_REAPER_TIMEOUT)")
    args = parser.parse_args(argv)

    timeout = _parse_timeout(args.timeout)
    full_scan = args.full_scan or _env_truthy(
        os.environ.get("TORTOISE_REAPER_FULL_SCAN"))

    # Singleton lock: second concurrent instance exits 0 with message.
    lock = _ReaperLock()
    if not lock.acquire():
        logger.warning("reaper already running (PID %s)",
                       _lock_holder_pid())
        return 0

    def _alarm_handler(signum, frame):
        logger.error("reaper timeout (%ss) exceeded — aborting sweep", timeout)
        sys.exit(1)

    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(timeout)
        acted = _run_sweep(dry_run=not args.no_dry_run,
                           batch_size=args.batch_size,
                           only_safe=args.only_safe,
                           jobs=args.jobs,
                           full_scan=full_scan)
        signal.alarm(0)
    finally:
        lock.release()

    if args.json:
        print(_json.dumps([
            {"pid": r.get("pid"), "socket_path": r.get("socket_path"),
             "pid_file": r.get("pid_file"),
             "dbdir": r.get("dbdir"),
             "removed_dir": r.get("removed_dir"),
             "quarantine_dir": r.get("quarantine_dir"),
             "classification": r.get("classification")}
            for r in acted
        ]))
    else:
        # #1642 FIX 1: the scheduled/standalone runs need a visible summary
        # (the sweep otherwise prints nothing when clean — a cron log that
        # never says anything cannot be verified).
        killed = sum(1 for r in acted if r.get("classification") == "candidate")
        stale = sum(1 for r in acted if r.get("classification") in (
            "stale_socket", "stale_quarantine", "stale_pid_file"))
        # #4068: a bounded scan that returned a partial set must never read
        # as a finished sweep (the silent-[] failure class this change
        # removes).
        truncated = ("" if getattr(acted, "complete", True)
                     else " — SCAN TRUNCATED (partial discovery)")
        print(f"[reaper] sweep complete: {len(acted)} acted "
              f"({killed} killed, {stale} stale cleaned){truncated}")
    return 0


# Dependency-free mirror of `tortoise.env_truthy.TRUTHY` (#4097). This module has NO
# intra-package module-level imports by design (see the module docstring), so it
# cannot import the shared leaf without dragging in `tortoise/__init__.py` ->
# redislite. A module-level delegate was measured to break the standalone import
# purity pinned by
# tests/test_env_truthy.py::test_reaper_standalone_import_stays_dependency_free;
# the mirror is held in lockstep with the contract by
# tests/test_env_truthy.py::test_reaper_mirror_matches_the_contract.
_ENV_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_truthy(raw: str | None) -> bool:
    """Truthy env value: {1,true,yes,on}, case-insensitive.

    #4068: `None` (unset) is False. Mirrors `tortoise.env_truthy.is_truthy` (see
    `_ENV_TRUTHY` above); the `--full-scan` flag takes precedence over the env var
    (the `_parse_timeout` CLI > env > default shape).
    """
    return raw is not None and str(raw).strip().lower() in _ENV_TRUTHY


def _lock_holder_pid() -> str:
    # #4098: never read through a planted symlink at the lock path, and never
    # BLOCK on one — this runs in main() BEFORE `signal.alarm(timeout)` is
    # armed, so `open(FIFO, O_RDONLY)` without O_NONBLOCK would hang reaper
    # startup forever with the watchdog disabled. A non-regular path is
    # "unknown": it can never be the lock this module wrote.
    #
    # #4098 review: `O_NOFOLLOW` alone protects only the BASENAME, so a
    # planted symlink at the lock DIR would still redirect the read (log
    # spoofing) and could serve an arbitrarily large file before the
    # watchdog is armed. Anchor on the dir fd and address the lock RELATIVE
    # to it, exactly like `_ReaperLock.acquire` — and cap the read, and
    # require the dir to be ours (a foreign-owned dir's contents are
    # attacker-authored).
    lock_dir = os.path.dirname(_LOCK_PATH)
    try:
        dir_fd = os.open(lock_dir,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError:
        return "unknown"
    try:
        try:
            if os.fstat(dir_fd).st_uid != os.geteuid():
                return "unknown"
        except OSError:
            return "unknown"
        try:
            fd = os.open(os.path.basename(_LOCK_PATH),
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600,
                         dir_fd=dir_fd)
        except OSError:
            return "unknown"
        try:
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    return "unknown"
            except OSError:
                return "unknown"
            try:
                chunk = os.read(fd, 64)   # a pid, not a file
            except OSError:
                return "unknown"
            return chunk.decode("utf-8", "replace").strip() or "unknown"
        finally:
            try:  # noqa: SIM105
                os.close(fd)
            except OSError:
                pass
    finally:
        try:  # noqa: SIM105
            os.close(dir_fd)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
