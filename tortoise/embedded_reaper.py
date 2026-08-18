"""Embedded redislite orphan reaper.

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
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path

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
# #1383 security review (Issue 3): ownership marker written into a
# quarantine at rename time — the sweep only rmtrees dirs carrying it, so a
# same-suffix foreign dir (another tool's temp naming, a planted decoy) is
# never touched.
REAPER_OWNED_MARKER = ".reaper-owned"
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


def active_suite_tokens() -> list[str]:
    """List active pytest-suite marker tokens (filenames in ACTIVE_SUITES_DIR).

    Each marker is created by a suite's conftest at session start and removed
    at session end. Stale markers (pid dead — suite SIGKILLed) are treated as
    absent so one crash cannot permanently degrade later suites' sweeps to
    only-safe (issue #1005 review P2). Empty when no other suite is mid-run.
    """
    try:
        entries = os.listdir(ACTIVE_SUITES_DIR)
    except OSError:
        return []
    tokens = []
    for e in entries:
        if e.startswith("."):
            continue
        try:
            text = Path(ACTIVE_SUITES_DIR, e).read_text()
        except OSError:
            continue
        if not text.strip():
            continue  # empty/partial marker (failed write) -> stale
        m = re.search(r"pid=(\d+)", text)
        if not m:
            continue  # no parsable pid -> stale
        try:
            pid = int(m.group(1))
            alive = _pid_alive(pid)
        except (ValueError, OverflowError, OSError):
            continue  # malformed pid -> stale, never fail the sweep
        if not alive:
            continue  # stale marker from a killed suite
        tokens.append(e)
    return tokens


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


def _real_gettempdir() -> str:
    return os.path.realpath(tempfile.gettempdir())


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
    try:
        out = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    etime = out.stdout.strip()
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
    except (ProcessLookupError, PermissionError):
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


def _registry_owner_alive(registry: dict | None) -> bool | None:
    """Liveness of the registry-recorded owning process (issue #1427).

    The registry records a pidfile path; the pid file's content is the
    server's owning process pid. Returns True when that pid is alive,
    False when provably dead (orphan), None when unresolvable (no
    pidfile, pid file missing/unreadable, unparseable content). Callers
    must fail closed on None — an unknown owner keeps protection.
    """
    if not registry or not registry.get("pidfile"):
        return None
    try:
        pid = int(Path(registry["pidfile"]).read_text().strip())
    except (OSError, ValueError, TypeError):
        return None
    if not _pid_alive(pid):
        return False
    return True


def _is_detached(pid: int) -> bool:
    """True when the process's direct parent is init (pid 0/1) — its whole
    spawning tree has exited and it was reparented, so NO live process holds
    it. Such a server is a dead-end: safe to reap even while OTHER suites run
    (issue #1115 option A — bounds orphan accumulation under concurrent
    suites without the global 'last suite standing' wait).

    Fail-closed: any uncertainty (ps timeout/error, pid vanished, unparseable
    ppid) returns False so the server is protected, never risked. A server
    whose parent is still alive (in-process fixture server, live test
    subprocess) is also protected — only fully reparented servers qualify.
    """
    import subprocess as _sp
    try:
        r = _sp.run(["ps", "-o", "ppid=", "-p", str(pid)],
                    capture_output=True, text=True, timeout=5)
    except (_sp.TimeoutExpired, OSError):
        return False
    raw = r.stdout.strip()
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
    try:
        out = subprocess.run(
            ["lsof", "-Fp", "-a", "-p", str(pid), "-U"],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    # -Fp prints 'p<pid>' entries; presence means it has unix sockets open.
    # Additionally verify cmdline contains the socket path for certainty.
    return f"p{pid}" in out.stdout


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
    for line in out.stdout.splitlines():
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
    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


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
        except socket.timeout:
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
        try:
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
            return _parse_client_list(out.stdout if out.stdout else "")
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
        except socket.timeout:
            return None, "timeout"  # connect-phase timeout — retryable too
        except OSError:
            return None, "error"
        try:
            s.sendall(b"*2\r\n$6\r\nCLIENT\r\n$4\r\nLIST\r\n")
            raw = _read_resp_reply(s)
        except socket.timeout:
            return None, "timeout"  # read-phase timeout — the retry target
        except OSError:
            return None, "error"
    finally:
        if s is not None:
            try:
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
    """One ps call for all pids: {pid: {cmdline, etime}}."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-ww", "-o", "pid=,etime=,command=",
             "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    result: dict[int, dict] = {}
    for line in out.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        result[pid] = {"etime": _parse_etime(parts[1]),
                       "cmdline": parts[2] if len(parts) > 2 else ""}
    return result


def _pgrep_redis_servers() -> list[int]:
    """Live redislite redis-server PIDs via pgrep (issue #1005 perf).

    O(servers) instead of scanning the whole tempdir (which holds tens of
    thousands of stale dirs on a leaky machine). Returns [] when pgrep is
    unavailable.
    """
    try:
        out = subprocess.run(
            ["pgrep", "-f", "redislite/bin/redis-server"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
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


def discover(jobs: int = 1, max_tempdir_entries: int = 5000) -> list[dict]:
    """Scan for redislite orphans; return classified records.

    Two passes (issue #1005 perf — the tempdir accumulates tens of thousands
    of stale dirs, making a full walk minutes-long under load):
      1. Live servers via pgrep + cmdline unixsocket extraction — O(servers).
      2. Tempdir walk for stale sockets / synthetic dirs, ONLY when the
         tempdir is small (st_nlink <= max_tempdir_entries). On leaky
         machines the walk is skipped with a warning — stale-socket
         detection degrades gracefully instead of stalling the sweep.

    jobs>1 parallelizes per-dir classification. Fail-closed semantics are
    per-record and unchanged under parallelism.
    """
    results = []
    tmpdir = _real_gettempdir()

    # Pass 1: live servers (authoritative pid comes from pgrep).
    live_pids = _pgrep_redis_servers()
    seen_dirs: set[str] = set()

    # One batched ps for all live pids (issue #1005 perf).
    global _PROC_INFO_CACHE
    _PROC_INFO_CACHE = _batch_process_info(live_pids)
    try:
        return _discover_from_live(live_pids, jobs, tmpdir, seen_dirs,
                                   max_tempdir_entries)
    finally:
        _PROC_INFO_CACHE = {}


def _discover_from_live(live_pids, jobs, tmpdir, seen_dirs,
                        max_tempdir_entries):
    """Classification half of discover() (separated so the proc-info cache
    has a deterministic lifetime)."""
    results = []

    def _classify_live(pid: int) -> dict | None:
        sock_dir = _socket_dir_from_cmdline(pid)
        if not sock_dir:
            return None
        socket_path = os.path.join(sock_dir, "redis.socket")
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

    # Pass 2: tempdir walk (stale sockets + synthetic dirs in tests). Skip
    # when the tempdir is pathologically large — the walk is the cost that
    # made sweeps stall at 64k dirs (issue #1005).
    try:
        entry_count = os.stat(tmpdir).st_nlink
    except OSError:
        entry_count = 0
    if entry_count > max_tempdir_entries:
        logger.warning(
            "tempdir has %s entries — skipping stale-socket walk "
            "(issue #1005 perf guard)", entry_count)
        return results

    try:
        entries = list(os.scandir(tmpdir))
    except (PermissionError, OSError) as e:
        logger.warning("permission denied scanning tempdir %s: %s", tmpdir, e)
        return results

    dirs = []
    for entry in entries:
        if not entry.is_dir():
            continue
        # #1383 plan-review P1: reaper-owned quarantine dirs (*.reaper-
        # stale-*) are handled exclusively by _sweep_quarantine_dirs —
        # never classify them (a guard-7-preserved LIVE server in a moved
        # dir would otherwise classify 'candidate' and be KILLED).
        if STALE_QUARANTINE_SUFFIX in entry.name:
            continue
        try:
            socket_path = os.path.join(entry.path, "redis.socket")
            if not os.path.exists(socket_path):
                continue
            if os.path.realpath(entry.path) in seen_dirs:
                continue
            dirs.append((entry.path, socket_path))
        except (PermissionError, OSError):
            logger.warning("dir skipped (OSError): %s", entry.path)
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
    return results


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
        "dir_missing": dir_missing,
        "client_count": client_count,
        "uptime": uptime,
        "classification": classification,
        "settings": registry,
    }


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
        # auto-generated dirname, no .db file -> could be a no-path server
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
        # Issue #1427: a path-based server whose registry-recorded owner pid
        # is provably dead is an orphan — the db file has no live owner, so
        # the server is a leftover, not live data. Fall through to the
        # cooldown/candidate path (dead pid -> candidate); live owners keep
        # the protection below, unresolvable owners fail closed.
        if _registry_owner_alive(registry) is False:
            return _cooldown_check(registry)
        return "protected"

    # Signal 2: dbfilename is the generic 'redis.db' (no-path) vs user name.
    # Issue #1005: in an ephemeral TEST tree the filename signal does not
    # protect — the tree is disposable regardless of the db filename.
    db_filename = registry.get("dbfilename", "")
    if db_filename and db_filename != "redis.db" \
            and not _is_ephemeral_dir(reg_dbdir_real, tmpdir_real):
        # Issue #1427: same orphan reclassification as Signal 1 — a dead
        # registry-recorded owner makes this a leftover, not live data.
        if _registry_owner_alive(registry) is False:
            return _cooldown_check(registry)
        return "protected"

    # Old-format registry (no dbfilename field): .db file present -> protected.
    if "dbfilename" in registry and registry.get("dbfilename") is None:
        if _dir_has_db_file(dbdir_real) and not _is_ephemeral_dir(dbdir_real, tmpdir_real):
            # Issue #1427: same orphan reclassification — old-format
            # path-based servers are the same protection class.
            if _registry_owner_alive(registry) is False:
                return _cooldown_check(registry)
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
    if pid is not None and not _pid_alive(pid):
        return "stale_socket"
    uptime = _uptime_seconds(pid) if pid else 0.0
    min_uptime = _parse_min_uptime()
    if uptime is not None and uptime < min_uptime:
        return "protected"
    return "candidate"


def _dir_has_db_file(dbdir: str) -> bool:
    try:
        for p in Path(dbdir).glob("*.db"):
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
         only_safe: bool = False) -> list[dict]:
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

    only_safe=True (issue #1005 concurrency guard): restrict KILLS to
    dir-gone (dir_missing) candidates — the owning suite's tmp tree was
    cleaned, so the server cannot belong to a running suite — plus DETACHED
    candidates (issue #1115): a live ephemeral server whose direct parent
    is init was reparented after its whole spawning tree exited, so no live
    process holds it; reaping it cannot disturb a running suite. Live
    ephemeral servers with 0 clients still owned by a live process are
    skipped so a concurrent suite's between-tests idle server is never
    killed. (Unchanged #1005/#1115 behavior for LIVE candidates.)
    """
    acted = []
    killed = 0
    stale_removed = 0  # #1383: stale removals budgeted separately from kills
    for record in records:
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
                              or _is_detached(record.get("pid") or 0)):
            logger.info(
                "concurrent-suite guard: skipping live ephemeral candidate %s",
                record.get("socket_path"))
            continue

        # Liveness-first: never kill a dead PID's leftovers via connect.
        if not record.get("pid") or not _pid_alive(record["pid"]):
            logger.warning("dead pid, skipping: %s",
                           record.get("socket_path"))
            continue

        # Double-check CLIENT LIST (before+after).
        clients_before = _active_client_count(record["socket_path"])
        if clients_before is None:
            logger.warning(
                "CLIENT LIST probe failed, skipping (fail closed): %s",
                record.get("socket_path"))
            continue
        if clients_before > 0:
            logger.info("server has %d active client(s), skipping: %s",
                        clients_before, record["socket_path"])
            continue
        clients_after = _active_client_count(record["socket_path"])
        if clients_after is None:
            logger.warning(
                "CLIENT LIST re-probe failed, skipping (fail closed): %s",
                record.get("socket_path"))
            continue
        if clients_after > 0:
            logger.info("server gained a client between checks, skipping: %s",
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
        dbdir = record.get("dbdir")
        if dbdir and _is_ephemeral_dir(
                os.path.realpath(dbdir), os.path.realpath(tempfile.gettempdir())):
            _cleanup_tempdir(dbdir)
        else:
            logger.warning("kill path: skipping tempdir cleanup for "
                           "non-ephemeral dbdir %r", dbdir)
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
    pid = None
    pidfile = os.path.join(dbdir_real, "redis.pid")
    try:
        pid = int(Path(pidfile).read_text().strip())
    except (OSError, ValueError):
        pid = None
    if pid is not None and _pid_alive(pid):
        logger.warning("stale dir pidfile now alive (%s), skipping: %s",
                       pid, dbdir_real)
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
    if dry_run:
        logger.warning("[DRY-RUN] would remove stale socket dir %s", dbdir_real)
        return record
    # Guard 6/7: atomic rename-aside then re-verify BEFORE the irreversible
    # rmtree. The renamed dir is the quarantine — if re-verification fails,
    # the dir stays for operator inspection / next-sweep convergence.
    renamed = dbdir_real + STALE_QUARANTINE_SUFFIX + str(time.time_ns())
    try:
        os.rename(dbdir_real, renamed)
    except OSError as exc:
        logger.warning("stale dir rename failed (%s), skipping: %s",
                       exc, dbdir_real)
        return None
    # #1383 security review (Issue 3): mark the quarantine as reaper-owned
    # so _sweep_quarantine_dirs never rmtrees a same-suffix foreign dir.
    try:
        with open(os.path.join(renamed, REAPER_OWNED_MARKER), "w") as fh:
            fh.write("reaper-owned\n")
    except OSError:
        logger.warning("could not write reaper marker, leaving quarantine: %s",
                       renamed)
        return None
    renamed_sock = os.path.join(renamed, "redis.socket")
    if not os.path.exists(renamed_sock):
        logger.warning("quarantined socket vanished, leaving dir: %s", renamed)
        return None  # leave quarantine (next sweep re-probes)
    if _probe_socket_any(renamed_sock, timeout=PROBE_SOCKET_TIMEOUT) != "dead":
        logger.warning("quarantined socket live, leaving dir: %s", renamed)
        return None  # live server in the moved dir — do NOT delete
    # Guard 8: pidfile written during the window (backlog-full ECONNREFUSED
    # hardening — a live server that refuses connects can still write its pid)
    try:
        moved_pid = int(Path(os.path.join(renamed, "redis.pid")).read_text().strip())
    except (OSError, ValueError):
        moved_pid = None
    if moved_pid is not None and _pid_alive(moved_pid):
        logger.warning("quarantined pidfile now alive (%s), leaving dir: %s",
                       moved_pid, renamed)
        return None
    _cleanup_tempdir(renamed)
    if os.path.exists(renamed):
        logger.warning("partial rmtree leftover, will re-probe next sweep: %s",
                       renamed)
    logger.warning("removed stale socket dir %s (was %s)", renamed, dbdir_real)
    # Acted record: `dbdir` stays the ORIGINAL (pre-rename) path so all
    # existing acted assertions hold; the renamed/quarantined path rides in
    # `removed_dir` for --json correlation (plan-review cycle 2).
    return {**record, "removed_dir": renamed}


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
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _cleanup_tempdir(dbdir: str | None) -> None:
    if not dbdir:
        return
    try:
        shutil.rmtree(dbdir, ignore_errors=True)
    except OSError:
        logger.warning("could not remove tempdir %s", dbdir)


# ── CLI + singleton lock + timeout (plan Task 3) ────────────────────

_LOCK_PATH = os.path.join(
    os.path.expanduser("~"), ".tortoise", ".reaper.lock")
TIMEOUT_DEFAULT = 120


class _ReaperLock:
    """fcntl-based exclusive lock; auto-released on process exit (incl. SIGKILL)."""

    def __init__(self, path: str = _LOCK_PATH):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        import fcntl
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        import fcntl
        if self._fh:
            try:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def _parse_timeout(cli_value: str | None) -> int:
    """Timeout resolution: CLI --timeout > TORTOISE_REAPER_TIMEOUT env > 120."""
    if cli_value is not None:
        try:
            return int(float(cli_value))
        except ValueError:
            logger.warning("invalid --timeout %r — using default", cli_value)
    env = os.environ.get("TORTOISE_REAPER_TIMEOUT", "")
    if env:
        try:
            return int(float(env))
        except ValueError:
            logger.warning(
                "TORTOISE_REAPER_TIMEOUT=%r invalid — using default", env)
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
    can remove up to 2xSTALE_SWEEP_BUDGET (plan-review cycle 2). Gated on
    the same tempdir walk-size guard; symlinked entries are skipped (mirror
    discover pass 2).
    """
    tmpdir = _real_gettempdir()
    try:
        entry_count = os.stat(tmpdir).st_nlink
    except OSError:
        entry_count = 0
    if entry_count > 5000:  # same perf guard as discover() pass 2
        return []
    removed = []
    try:
        entries = list(os.scandir(tmpdir))
    except (PermissionError, OSError):
        return removed
    for entry in entries:
        if not entry.is_dir() or entry.is_symlink():
            continue  # symlink safety mirrors discover pass 2 (cycle 2)
        if STALE_QUARANTINE_SUFFIX not in entry.name:
            continue
        if len(removed) >= budget:
            break
        q = entry.path
        # #1383 security review (Issue 3): only rmtree dirs carrying the
        # reaper-owned marker — a same-suffix foreign dir (another tool's
        # temp naming, a planted decoy) must never be touched. The marker
        # is written at rename-aside time in the stale action.
        if not os.path.exists(os.path.join(q, REAPER_OWNED_MARKER)):
            continue
        if not _is_ephemeral_dir(os.path.realpath(q),
                                 os.path.realpath(tempfile.gettempdir())):
            continue  # containment re-verify (defense in depth)
        qsock = os.path.join(q, "redis.socket")
        # Guard-8 equivalent (cycle-3 P1): a LIVE backlog-full server
        # answers ECONNREFUSED ('dead') — the moved pidfile is the
        # discriminator. A guard-8-preserved quarantine left by reap() in
        # the SAME sweep must never be rmtree'd here.
        try:
            qpid = int(Path(os.path.join(q, "redis.pid")).read_text().strip())
        except (OSError, ValueError):
            qpid = None
        if qpid is not None and _pid_alive(qpid):
            logger.warning("quarantined dir pidfile live (%s), leaving: %s",
                           qpid, q)
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
    return removed


def _run_sweep(dry_run: bool, batch_size: int | None, only_safe: bool = False,
               jobs: int = 8, kill_pacing: float = KILL_PACING_DEFAULT,
               sweep_pid_files: bool = True) -> list[dict]:
    """Discover + classify + reap; return acted-upon records.

    jobs>1 parallelizes the per-candidate CLIENT LIST probes (the dominant
    cost at hundreds of leaked servers — issue #1005); kills stay serial
    with pacing.

    NOTE: the reaper singleton lock is held by main() (CLI); direct callers
    (tests, conftest session hygiene) run unlocked — pre-existing contract,
    unchanged by #1383.
    """
    records = discover(jobs=jobs)
    # #1383: reapable classes are candidate (live orphan -> kill) and
    # stale_socket (dead-pid leftover dir -> guarded rmtree). Phase 1
    # resolves stale-pid records before any action.
    reapables = [r for r in records
                 if r["classification"] in ("candidate", "stale_socket")]
    resolved = [phase1_probe(r) for r in reapables]
    acted = reap(resolved, dry_run=dry_run, batch_size=batch_size,
                 kill_pacing=kill_pacing, only_safe=only_safe)
    # #1383: quarantine convergence (partial-rmtree/respawn leftovers)
    try:
        for q in _sweep_quarantine_dirs(dry_run=dry_run):
            acted.append({"pid": None, "quarantine_dir": q,
                          "classification": "stale_quarantine"})
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
    return acted


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
                        help="Only reap dir-gone/stale records (concurrent-suite "
                             "safe; issue #1005)")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable JSON output")
    parser.add_argument("--timeout", type=str, default=None,
                        help=f"Sweep timeout in seconds (default "
                             f"{TIMEOUT_DEFAULT}; env TORTOISE_REAPER_TIMEOUT)")
    args = parser.parse_args(argv)

    timeout = _parse_timeout(args.timeout)

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
                           jobs=args.jobs)
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
    return 0


def _lock_holder_pid() -> str:
    try:
        with open(_LOCK_PATH) as fh:
            return fh.read().strip() or "unknown"
    except OSError:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
