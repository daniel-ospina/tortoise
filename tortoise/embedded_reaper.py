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
_AUTOGEN_DIRNAME = re.compile(r"^(redislite_|tmp)[a-zA-Z0-9_]+$")


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
    preferred but ps -o etime works on both.
    """
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


def _client_list(socket_path: str) -> list[dict]:
    """CLIENT LIST over the unix socket using a raw RESP connection.

    Uses redislite's own client (which is a redis client) via subprocess-free
    approach: we shell to redis-cli if available, else parse via raw RESP.
    Returns list of client dicts; empty on failure.
    """
    # Prefer redis-cli (no python redis dependency, no spawn risk).
    try:
        out = subprocess.run(
            ["redis-cli", "-s", socket_path, "CLIENT", "LIST"],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT,
        )
        if out.returncode == 0 and out.stdout.strip():
            return _parse_client_list(out.stdout)
    except (subprocess.TimeoutExpired, OSError):
        pass
    # Fallback: raw RESP via the redislite client API (execute_command).
    try:
        from redislite.falkordb_client import FalkorDB
        db = FalkorDB(unix_socket_path=socket_path)
        try:
            raw = db.execute_command("CLIENT", "LIST")
            return _parse_client_list(raw if isinstance(raw, str) else "")
        finally:
            try:
                db.close()
            except Exception:
                pass
    except Exception:
        return []
    return []


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


def _active_client_count(socket_path: str) -> int:
    """Count non-reaper clients (SKIPME semantics).

    Our probing connection (redis-cli) is the freshly-created one with
    age ~0 and idle ~0. Any connection with age >= 2s is a pre-existing
    real client. Named clients are also real users regardless of age.
    """
    clients = _client_list(socket_path)
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


def discover() -> list[dict]:
    """Scan tempdir for redislite orphans; return classified records.

    Each record: {pid, socket_path, dbdir, client_count, uptime,
    classification, settings}.
    """
    results = []
    tmpdir = _real_gettempdir()

    try:
        entries = list(os.scandir(tmpdir))
    except (PermissionError, OSError) as e:
        logger.warning("permission denied scanning tempdir %s: %s", tmpdir, e)
        return results

    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            socket_path = os.path.join(entry.path, "redis.socket")
            if not os.path.exists(socket_path):
                continue
            record = _classify_dir(entry.path, socket_path)
            if record is not None:
                results.append(record)
        except PermissionError:
            logger.warning("permission denied dir skipped: %s", entry.path)
            continue
        except OSError:
            logger.warning("dir skipped (OSError): %s", entry.path)
            continue
    return results


def _classify_dir(dbdir: str, socket_path: str) -> dict | None:
    """Classify a single candidate dir. Returns record or None (skip)."""
    registry = _registry_for(dbdir)
    dbdir_real = os.path.realpath(dbdir)
    socket_real = os.path.realpath(socket_path)
    tmpdir_real = _real_gettempdir()

    classification = _classify(socket_real, dbdir_real, tmpdir_real, registry)
    pid = None
    if registry and registry.get("pidfile"):
        try:
            pid = int(Path(registry["pidfile"]).read_text().strip())
        except (OSError, ValueError):
            pid = None

    uptime = _uptime_seconds(pid) if pid else None
    client_count = 0
    if classification == "candidate" and pid and _pid_alive(pid):
        client_count = _active_client_count(socket_real)

    return {
        "pid": pid,
        "socket_path": socket_real,
        "dbdir": dbdir_real,
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
        if _dir_has_db_file(dbdir_real):
            return "protected"
        basename = os.path.basename(dbdir_real)
        if not _AUTOGEN_DIRNAME.match(basename):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", dbdir_real)
            return "protected"
        # auto-generated dirname, no .db file -> could be a no-path server
        return _cooldown_check(registry)

    # Signal 1: registry 'dir' is a USER dir (not auto tempdir) -> path-based.
    reg_dbdir = registry.get("dir", registry.get("dbdir", ""))
    reg_dbdir_real = os.path.realpath(reg_dbdir) if reg_dbdir else ""
    is_autogen_dbdir = bool(
        reg_dbdir_real
        and reg_dbdir_real.startswith(tmpdir_real)
        and _AUTOGEN_DIRNAME.match(os.path.basename(reg_dbdir_real))
    )
    if not is_autogen_dbdir and reg_dbdir_real:
        if reg_dbdir_real.startswith(tmpdir_real):
            logger.warning(
                "unrecognized dir pattern, treating as protected: %s", reg_dbdir_real)
        return "protected"

    # Signal 2: dbfilename is the generic 'redis.db' (no-path) vs user name.
    db_filename = registry.get("dbfilename", "")
    if db_filename and db_filename != "redis.db":
        return "protected"

    # Old-format registry (no dbfilename field): .db file present -> protected.
    if "dbfilename" in registry and registry.get("dbfilename") is None:
        if _dir_has_db_file(dbdir_real):
            return "protected"
        basename = os.path.basename(dbdir_real)
        if not _AUTOGEN_DIRNAME.match(basename):
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
         sigterm_timeout: float = 10.0) -> list[dict]:
    """Reap candidate records safely (plan Task 2). Returns acted-upon list.

    Only 'candidate' records are killed, and only after CLIENT LIST shows 0
    active clients (double-checked). Path-based / protected / stale_socket /
    undetermined records are NEVER killed here.
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

        # Liveness-first: never kill a dead PID's leftovers via connect.
        if not record.get("pid") or not _pid_alive(record["pid"]):
            logger.warning("dead socket connect failure, skipping: %s",
                           record.get("socket_path"))
            continue

        # Double-check CLIENT LIST (before+after).
        clients_before = _active_client_count(record["socket_path"])
        if clients_before > 0:
            logger.info("server has %d active client(s), skipping: %s",
                        clients_before, record["socket_path"])
            continue
        clients_after = _active_client_count(record["socket_path"])
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
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
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


def _run_sweep(dry_run: bool, batch_size: int | None) -> list[dict]:
    """Discover + classify + reap; return acted-upon records."""
    records = discover()
    candidates = [r for r in records if r["classification"] == "candidate"]
    # Phase 1: resolve stale-PID records via probe before any kill
    resolved = [phase1_probe(r) for r in candidates]
    return reap(resolved, dry_run=dry_run, batch_size=batch_size)


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
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Limit kills per run")
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
                           batch_size=args.batch_size)
        signal.alarm(0)
    finally:
        lock.release()

    if args.json:
        print(_json.dumps([
            {"pid": r.get("pid"), "socket_path": r.get("socket_path"),
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
