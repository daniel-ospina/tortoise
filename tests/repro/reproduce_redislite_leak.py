"""Reproduce the redislite process leak (issue #176).

Run:  python3 tests/repro/reproduce_redislite_leak.py
Cleanup: automatic (closes all connections, kills stragglers).

Demonstrates four behaviors empirically:
  1. SAME-PATH reuse: two connections with one stable db path -> 1 redis-server
  2. CROSS-PROCESS reuse: two processes, same path -> 1 redis-server (PID2 reuses PID1)
  3. NO-PATH leak: two connections without a path -> 2 redis-servers (the leak)
  4. STALE-PID recovery: SIGKILL the server, reconnect same path -> respawn (1 server)

Requires: pip install redislite
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

DB_PATH = os.path.join(tempfile.gettempdir(), "repro_176.db")


def server_socket_for(db_path: str) -> str:
    """Read the recorded unix socket from redislite's settings registry."""
    settings_file = db_path + ".settings"
    if os.path.exists(settings_file):
        with open(settings_file) as fh:
            return json.load(fh).get("unixsocket", "")
    return ""


def count_servers(socket_path: str) -> int:
    """Count redis-server processes bound to a specific socket path."""
    if not socket_path:
        return 0
    out = subprocess.run(
        ["ps", "-eo", "args"], capture_output=True, text=True
    ).stdout
    return sum(1 for line in out.splitlines()
               if "redis-server" in line and socket_path in line)


def count_all_redislite_servers() -> int:
    out = subprocess.run(
        ["ps", "-eo", "args"], capture_output=True, text=True
    ).stdout
    return sum(1 for line in out.splitlines()
               if "redislite/bin/redis-server" in line)


def test_same_path_reuse():
    from redislite.falkordb_client import FalkorDB
    db1 = FalkorDB(DB_PATH)
    time.sleep(1)
    sock = server_socket_for(DB_PATH)
    n1 = count_servers(sock)
    db2 = FalkorDB(DB_PATH)  # same path -> must reuse
    time.sleep(1)
    n2 = count_servers(sock)
    db1.close(); db2.close()
    ok = n1 == 1 and n2 == 1
    print(f"[same-path] 2 connections same path -> {n2} server(s) "
          f"(expect 1: {'PASS' if ok else 'FAIL'})")
    return ok


def test_cross_process_reuse():
    code1 = (
        "import os,time; os.environ.pop('TORTOISE_DB_URI',None);\n"
        f"from redislite.falkordb_client import FalkorDB; db=FalkorDB({DB_PATH!r});\n"
        "time.sleep(10)"
    )
    proc = subprocess.Popen([sys.executable, "-c", code1])
    time.sleep(3)
    sock = server_socket_for(DB_PATH)
    n1 = count_servers(sock)
    code2 = (
        "import os; os.environ.pop('TORTOISE_DB_URI',None);\n"
        f"from redislite.falkordb_client import FalkorDB; db=FalkorDB({DB_PATH!r})"
    )
    subprocess.run([sys.executable, "-c", code2], timeout=30)
    time.sleep(1)
    n2 = count_servers(sock)
    proc.terminate(); proc.wait(timeout=10)
    ok = n1 == 1 and n2 == 1
    print(f"[cross-proc] 2 processes same path -> {n2} server(s) "
          f"(expect 1: {'PASS' if ok else 'FAIL'})")
    return ok


def test_no_path_leak():
    from redislite.falkordb_client import FalkorDB
    before = count_all_redislite_servers()
    db1 = FalkorDB()  # no path -> fresh tempdir server
    time.sleep(1)
    db2 = FalkorDB()  # no path -> another fresh tempdir server
    time.sleep(1)
    after = count_all_redislite_servers()
    leaked = after - before
    db1.close(); db2.close()
    ok = leaked >= 2
    print(f"[no-path] {before}->{after} servers (+{leaked}) for 2 no-path "
          f"connections (expect +2: {'PASS' if ok else 'FAIL'})")
    return ok


def test_stale_pid_recovery():
    from redislite.falkordb_client import FalkorDB
    db = FalkorDB(DB_PATH)
    time.sleep(1)
    settings_file = DB_PATH + ".settings"
    with open(settings_file) as fh:
        settings = json.load(fh)
    pid = int(open(settings["pidfile"]).read().strip())
    os.kill(pid, signal.SIGKILL)
    time.sleep(1)
    sock = settings.get("unixsocket", "")
    n_dead = count_servers(sock)
    db2 = FalkorDB(DB_PATH)  # must respawn (or reuse), not crash
    time.sleep(2)
    sock2 = server_socket_for(DB_PATH)
    n_after = count_servers(sock2)
    db.close() if False else None
    db2.close()
    ok = n_dead == 0 and n_after >= 1
    print(f"[stale-pid] after SIGKILL: {n_dead} server(s) on old socket, "
          f"reconnect -> {n_after} server(s) (no crash: {'PASS' if ok else 'FAIL'})")
    return ok


if __name__ == "__main__":
    # clean any leftover from prior runs
    subprocess.run(["pkill", "-f", "repro_176"], capture_output=True)
    results = {
        "same_path_reuse": test_same_path_reuse(),
        "cross_process_reuse": test_cross_process_reuse(),
        "no_path_leak": test_no_path_leak(),
        "stale_pid_recovery": test_stale_pid_recovery(),
    }
    for k, v in results.items():
        print(f"{k}: {'PASS' if v else 'FAIL'}")
    ok = all(results.values())
    subprocess.run(["pkill", "-f", "repro_176"], capture_output=True)
    sys.exit(0 if ok else 1)
