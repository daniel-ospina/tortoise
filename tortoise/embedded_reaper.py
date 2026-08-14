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

NEVER_KILL: anything classified protected (stable singleton, path-based
servers, boot-cooldown servers, unknown patterns). Only candidates may be
reaped, and only after liveness + CLIENT LIST verification (see reap()).

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
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


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
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def _probe_socket(socket_path: str) -> str:
    """Raw unix-socket connect probe (never redis-py — can't spawn).

    Returns 'dead' (ECONNREFUSED / no listener), 'alive' (accepts), or
    'undetermined' (timeout / error).
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(PROBE_TIMEOUT)
        try:
            s.connect(socket_path)
            return "alive"
        except (ConnectionRefusedError, FileNotFoundError):
            return "dead"
        except socket.timeout:
            return "undetermined"
        except OSError:
            return "undetermined"
        finally:
            s.close()
    except OSError:
        return "undetermined"


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

    Returns parsed clients, or None on any connect/read/parse failure so
    callers fail closed (never reap a server whose client state is unknown).
    """
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(PROBE_SOCKET_TIMEOUT)
        try:
            s.connect(socket_path)
            s.sendall(b"*2\r\n$6\r\nCLIENT\r\n$4\r\nLIST\r\n")
            raw = _read_resp_reply(s)
        finally:
            s.close()
    except OSError:  # includes socket.timeout (Python 3.10+)
        return None
    if raw is None:
        return None
    return _parse_client_list(raw)


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
            ["ps", "-o", "pid=,etime=,command=",
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
    """Extract the unixsocket dir from a redis-server cmdline."""
    cmdline = _cmdline(pid)
    m = _UNIXSOCKET_RE.search(cmdline)
    if not m:
        return None
    sock = m.group(1)
    return os.path.dirname(os.path.realpath(sock))


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
        rec = _classify_dir(sock_dir, socket_path)
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


def _classify_dir(dbdir: str, socket_path: str) -> dict | None:
    """Classify a single candidate dir. Returns record or None (skip)."""
    registry = _registry_for(dbdir)
    dbdir_real = os.path.realpath(dbdir)
    socket_real = os.path.realpath(socket_path)
    tmpdir_real = _real_gettempdir()

    classification = _classify(socket_real, dbdir_real, tmpdir_real, registry)
    # Issue #1005: servers whose registry dir is gone (pytest cleaned the tmp
    # tree at session end) are always reaping-safe — the owning suite ended.
    reg_dir = (registry or {}).get("dir", (registry or {}).get("dbdir", ""))
    dir_missing = _dir_missing_on_disk(reg_dir)
    pid = None
    if registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None

    uptime = _uptime_seconds(pid) if pid else None
    client_count = 0
    if classification == "candidate" and pid and _pid_alive(pid):
        # None (probe failed) means unknown — reap() will fail closed.
        # Keep the record field an int (None is internal to the probe).
        cc = _active_client_count(socket_real)
        client_count = 0 if cc is None else cc

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
              registry: dict | None) -> str:
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
        return _cooldown_check(registry)

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
        return "protected"

    # Signal 2: dbfilename is the generic 'redis.db' (no-path) vs user name.
    # Issue #1005: in an ephemeral TEST tree the filename signal does not
    # protect — the tree is disposable regardless of the db filename.
    db_filename = registry.get("dbfilename", "")
    if db_filename and db_filename != "redis.db" \
            and not _is_ephemeral_dir(reg_dbdir_real, tmpdir_real):
        return "protected"

    # Old-format registry (no dbfilename field): .db file present -> protected.
    if "dbfilename" in registry and registry.get("dbfilename") is None:
        if _dir_has_db_file(dbdir_real) and not _is_ephemeral_dir(dbdir_real, tmpdir_real):
            return "protected"
        basename = os.path.basename(dbdir_real)
        if not (_AUTOGEN_DIRNAME.match(basename)
                or _is_ephemeral_dir(dbdir_real, tmpdir_real)):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", dbdir_real)
            return "protected"

    return _cooldown_check(registry)


def _cooldown_check(registry: dict | None) -> str:
    """Boot-cooldown: fresh servers (uptime < MIN_UPTIME) are protected."""
    pid = None
    if registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None
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

    Returns record with updated classification:
      - stale_PID + probe dead   -> 'stale_socket' (removable)
      - stale_PID + probe alive  -> live, real PID derived
      - stale_PID + probe undetermined -> 'undetermined'
      - pid alive                -> unchanged
    """
    if record.get("pid") and _pid_alive(record["pid"]):
        return record
    if not record.get("socket_path"):
        return record
    probe = _probe_socket(record["socket_path"])
    if probe == "dead":
        record["classification"] = "stale_socket"
    elif probe == "alive":
        real_pid = _derive_real_pid(record["socket_path"], record.get("pid"))
        if real_pid:
            record["pid"] = real_pid
            record["classification"] = record.get("classification")
        else:
            record["classification"] = "undetermined"
    else:  # undetermined
        record["classification"] = "undetermined"
    return record


def reap(records: list[dict], dry_run: bool = True, batch_size: int | None = None,
         sigterm_timeout: float = 10.0, kill_pacing: float = KILL_PACING_DEFAULT,
         only_safe: bool = False) -> list[dict]:
    """Reap candidate records safely (plan Task 2). Returns acted-upon list.

    Only 'candidate' records are killed, and only after CLIENT LIST shows 0
    active clients (double-checked). Path-based / protected / stale_socket /
    undetermined records are NEVER killed here.

    only_safe=True (issue #1005 concurrency guard): restrict to dir-gone
    (dir_missing) candidates — the owning suite's tmp tree was cleaned, so
    the server cannot belong to a running suite — plus DETACHED candidates
    (issue #1115): a live ephemeral server whose direct parent is init was
    reparented after its whole spawning tree exited, so no live process
    holds it; reaping it cannot disturb a running suite. Live ephemeral
    servers with 0 clients still owned by a live process are skipped so a
    concurrent suite's between-tests idle server is never killed.
    """
    acted = []
    killed = 0
    for record in records:
        if batch_size is not None and killed >= batch_size:
            break
        classification = record.get("classification")
        if classification != "candidate":
            if classification in ("protected", "stale_socket", "undetermined"):
                logger.warning(
                    "skipping path-based/non-candidate server: %s",
                    record.get("socket_path"))
            continue
        if only_safe and not (record.get("dir_missing")
                              or classification == "stale_socket"
                              or _is_detached(record.get("pid") or 0)):
            logger.info(
                "concurrent-suite guard: skipping live ephemeral candidate %s",
                record.get("socket_path"))
            continue

        # Liveness-first: never kill a dead PID's leftovers via connect.
        if not record.get("pid") or not _pid_alive(record["pid"]):
            logger.warning("dead socket connect failure, skipping: %s",
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
        _cleanup_tempdir(record.get("dbdir"))
        logger.warning("killed orphan PID %s (%s)",
                       record["pid"], record["socket_path"])
        acted.append(record)
        killed += 1
        if kill_pacing > 0:
            time.sleep(kill_pacing)  # avoid synchronized shutdown bursts (#1005)
    return acted


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


def _run_sweep(dry_run: bool, batch_size: int | None, only_safe: bool = False,
               jobs: int = 8, kill_pacing: float = KILL_PACING_DEFAULT,
               sweep_pid_files: bool = True) -> list[dict]:
    """Discover + classify + reap; return acted-upon records.

    jobs>1 parallelizes the per-candidate CLIENT LIST probes (the dominant
    cost at hundreds of leaked servers — issue #1005); kills stay serial
    with pacing.
    """
    records = discover(jobs=jobs)
    candidates = [r for r in records if r["classification"] == "candidate"]
    # Phase 1: resolve stale-PID records via probe before any kill
    resolved = [phase1_probe(r) for r in candidates]
    acted = reap(resolved, dry_run=dry_run, batch_size=batch_size,
                 kill_pacing=kill_pacing, only_safe=only_safe)
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
